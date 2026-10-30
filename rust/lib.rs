//! Desktop-independent update checks, policy, process execution and diagnostics.
pub mod config;
pub mod diagnostics;
pub mod presentation;
pub mod process;
#[cfg(test)]
mod tests;
pub mod updates;
pub mod voice;
