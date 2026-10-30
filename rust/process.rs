use std::{
    io::{Read, Write},
    os::unix::process::CommandExt,
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Debug, Clone)]
pub struct CommandSpec {
    pub program: String,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
}
impl CommandSpec {
    pub fn new(program: &str, args: &[&str]) -> Self {
        Self {
            program: program.into(),
            args: args.iter().map(|s| (*s).into()).collect(),
            env: vec![],
        }
    }
}
#[derive(Debug, Clone)]
pub struct Output {
    pub code: Option<i32>,
    pub text: String,
    pub cancelled: bool,
    pub timed_out: bool,
}
/// Drain both pipes concurrently. Never wait for a newline to enforce a deadline.
/// Cancellation signals the process group and waits for process exit before returning.
pub fn run(
    spec: &CommandSpec,
    timeout: u64,
    cancel: Arc<AtomicBool>,
    input: Option<&[u8]>,
    mut emit: impl FnMut(&str),
) -> Result<Output, String> {
    if cancel.load(Ordering::SeqCst) {
        return Ok(Output {
            code: None,
            text: String::new(),
            cancelled: true,
            timed_out: false,
        });
    }
    let privileged =
        spec.program == "/usr/bin/pkexec" && spec.args == [crate::updates::HELPER, "pacman"];
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .envs(spec.env.iter().cloned())
        .stdin(if input.is_some() || privileged {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let mut child = command
        .spawn()
        .map_err(|e| format!("{}: {e}", spec.program))?;
    if let Some(bytes) = input {
        let bytes = bytes.to_vec();
        let mut pipe = child.stdin.take().unwrap();
        thread::spawn(move || {
            let _ = pipe.write_all(&bytes);
        });
    }
    let mut cancellation_pipe = if privileged { child.stdin.take() } else { None };
    let (tx, rx) = mpsc::channel();
    let pipes: Vec<Box<dyn Read + Send>> = vec![
        Box::new(child.stdout.take().unwrap()),
        Box::new(child.stderr.take().unwrap()),
    ];
    for mut pipe in pipes {
        let tx = tx.clone();
        thread::spawn(move || {
            let mut buf = [0; 4096];
            loop {
                match pipe.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        if tx.send(buf[..n].to_vec()).is_err() {
                            break;
                        }
                    }
                }
            }
        });
    }
    drop(tx);
    let start = Instant::now();
    let mut interrupted = None;
    let mut timed_out = false;
    let mut cancelled = false;
    let mut text = String::new();
    let status = loop {
        for _ in 0..128 {
            let Ok(bytes) = rx.try_recv() else {
                break;
            };
            let part = String::from_utf8_lossy(&bytes);
            emit(&part);
            append_tail(&mut text, &part);
        }
        // Reap before signalling, so a reused process ID cannot be targeted.
        if let Some(s) = child.try_wait().map_err(|e| e.to_string())? {
            break s;
        }
        if interrupted.is_none()
            && (cancel.load(Ordering::SeqCst) || start.elapsed().as_secs() >= timeout)
        {
            cancelled = cancel.load(Ordering::SeqCst);
            timed_out = !cancelled;
            interrupted = Some(Instant::now());
            // EOF tells the privileged supervisor to cancel its own root child.
            // The desktop cannot signal a root-owned Pacman directly.
            drop(cancellation_pipe.take());
            // SAFETY: the live child leads the group created by process_group(0).
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGINT);
            }
        }
        if let Some(at) = interrupted {
            let seconds = at.elapsed().as_secs();
            if seconds >= 5 {
                unsafe {
                    libc::kill(
                        -(child.id() as i32),
                        if seconds >= 10 {
                            libc::SIGKILL
                        } else {
                            libc::SIGTERM
                        },
                    );
                }
            }
        }
        thread::sleep(Duration::from_millis(30));
    };
    // EOF normally follows exit. Bound draining when descendants retain pipes.
    let drain_start = Instant::now();
    while let Ok(bytes) = rx.recv_timeout(Duration::from_millis(100)) {
        let part = String::from_utf8_lossy(&bytes);
        emit(&part);
        append_tail(&mut text, &part);
        if drain_start.elapsed().as_secs() >= 1 {
            break;
        }
    }
    Ok(Output {
        code: status.code(),
        text,
        cancelled,
        timed_out,
    })
}
fn append_tail(text: &mut String, part: &str) {
    text.push_str(part);
    if text.len() > 2 * 1024 * 1024 {
        let mut i = text.len() - 1024 * 1024;
        while !text.is_char_boundary(i) {
            i += 1;
        }
        text.drain(..i);
    }
}
pub fn exists(name: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::env::var_os("PATH").is_some_and(|p| {
        std::env::split_paths(&p).any(|d| {
            let p = d.join(name);
            p.is_file()
                && p.metadata()
                    .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
        })
    })
}

/// Supervise a child in its own process group. EOF/data on the control pipe
/// requests cancellation. Used by the root helper, and tested with harmless children.
pub fn supervise_child(
    mut child: std::process::Child,
    mut control: impl Read + Send + 'static,
) -> std::io::Result<std::process::ExitStatus> {
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    thread::spawn(move || {
        let mut byte = [0];
        let _ = control.read(&mut byte);
        flag.store(true, Ordering::SeqCst);
    });
    let mut interrupted: Option<Instant> = None;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if cancelled.load(Ordering::SeqCst) && interrupted.is_none() {
            interrupted = Some(Instant::now());
            // SAFETY: the child was created in its own process group and is unreaped.
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGINT);
            }
        }
        if let Some(at) = interrupted {
            let seconds = at.elapsed().as_secs();
            if seconds >= 5 {
                unsafe {
                    libc::kill(
                        -(child.id() as i32),
                        if seconds >= 10 {
                            libc::SIGKILL
                        } else {
                            libc::SIGTERM
                        },
                    );
                }
            }
        }
        thread::sleep(Duration::from_millis(30));
    }
}

/// Pipe binary output directly between two programs, checking both exit statuses.
/// No shell is involved. Both process groups are reaped on failures and deadlines.
pub fn pipeline(
    producer: &CommandSpec,
    consumer: &CommandSpec,
    input: &[u8],
    timeout: u64,
) -> Result<(), String> {
    pipeline_observed(producer, consumer, input, timeout, |_| {})
}

/// Relay PCM without changing its bytes; the observer receives sample-aligned
/// chunks after they have been submitted to the player. It must never block.
pub fn pipeline_observed(
    producer: &CommandSpec,
    consumer: &CommandSpec,
    input: &[u8],
    timeout: u64,
    mut observe: impl FnMut(&[u8]) + Send + 'static,
) -> Result<(), String> {
    let mut a = Command::new(&producer.program)
        .args(&producer.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut b = match Command::new(&consumer.program)
        .args(&consumer.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
    {
        Ok(b) => b,
        Err(e) => {
            let _ = a.kill();
            let _ = a.wait();
            return Err(e.to_string());
        }
    };
    let mut audio = a.stdout.take().unwrap();
    let mut player = b.stdin.take().unwrap();
    let relay = thread::spawn(move || -> Result<(), String> {
        // 1,102 mono PCM16 samples, approximately 50 ms at 22,050 Hz.
        let mut chunk = [0u8; 2204];
        loop {
            let mut used = 0;
            while used < chunk.len() {
                match audio.read(&mut chunk[used..]) {
                    Ok(0) => break,
                    Ok(n) => used += n,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(e) => return Err(e.to_string()),
                }
            }
            if used == 0 {
                return Ok(());
            }
            player
                .write_all(&chunk[..used])
                .map_err(|e| e.to_string())?;
            observe(&chunk[..used]);
        }
    });
    let mut stdin = a.stdin.take().unwrap();
    let bytes = input.to_vec();
    thread::spawn(move || {
        let _ = stdin.write_all(&bytes);
    });
    let start = Instant::now();
    let mut a_status = None;
    let mut b_status = None;
    loop {
        if a_status.is_none() {
            a_status = a.try_wait().map_err(|e| e.to_string())?;
        }
        if b_status.is_none() {
            b_status = b.try_wait().map_err(|e| e.to_string())?;
        }
        if let (Some(x), Some(y)) = (a_status, b_status) {
            let relayed = relay.join().map_err(|_| "Audio relay failed".to_string())?;
            return if x.success() && y.success() {
                relayed
            } else {
                Err(format!("Pipeline failed: synthesis {x}, player {y}"))
            };
        }
        if start.elapsed().as_secs() >= timeout {
            for (child, status) in [(&mut a, a_status), (&mut b, b_status)] {
                if status.is_none() {
                    unsafe {
                        libc::kill(-(child.id() as i32), libc::SIGKILL);
                    }
                    let _ = child.wait();
                }
            }
            let _ = relay.join();
            return Err("Piper/player pipeline timed out".into());
        }
        thread::sleep(Duration::from_millis(30));
    }
}
