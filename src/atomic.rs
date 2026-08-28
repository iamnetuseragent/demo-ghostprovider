//! Atomic, symlink-proof, mode-0600 file writes.
//!
//! `write_atomic` replaces the write-then-chmod pattern that allowed a
//! TOCTOU window and followed pre-planted symlinks: the data is written to a
//! random-named sibling temp file created with `O_EXCL` (never following an
//! existing symlink) at mode 0600 from the first byte, synced, and renamed
//! over the destination. The rename also guarantees readers never observe a
//! half-written file.
//!
//! This is used for everything that may hold private data: service
//! EnvironmentFiles, the deployment registry and systemd unit files.

use std::io::Write;
use std::path::Path;

/// Write `data` into `path` atomically with mode 0600. The parent directory
/// must already exist; `path` itself may or may not. On Unix the final file
/// gets mode 0600 regardless of what was there before.
pub fn write_atomic(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "write_atomic: path has no parent",
        )
    })?;
    let file_name = path.file_name().and_then(|s| s.to_str()).unwrap_or("out");

    for _ in 0..100 {
        let tmp = dir.join(format!("{file_name}.{}.tmp", random_hex(8)?));
        let write = (|| {
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&tmp)?;
                f.write_all(data)?;
                f.sync_all()?;
            }
            #[cfg(not(unix))]
            {
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&tmp)?;
                f.write_all(data)?;
                f.sync_all()?;
            }
            std::fs::rename(&tmp, path)
        })();
        match write {
            Ok(()) => return Ok(()),
            // O_EXCL collision with the random name: pick another.
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                let _ = std::fs::remove_file(&tmp);
                continue;
            }
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(e);
            }
        }
    }
    Err(std::io::Error::other(
        "write_atomic: could not create a unique temp file",
    ))
}

/// `bytes` random bytes from the kernel CSPRNG as lowercase hex. No weak
/// fallback: names derived from time+pid would be guessable, and the temp
/// files we gate with O_EXCL exist only to *not* trust predictability. A
/// failure to read `/dev/urandom` aborts instead of degrading.
pub fn random_hex(bytes: usize) -> std::io::Result<String> {
    use std::io::Read;
    let mut buf = vec![0u8; bytes];
    std::fs::File::open("/dev/urandom")?.read_exact(&mut buf)?;
    Ok(buf.iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn tmp(tag: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "dgp-atomic-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .subsec_nanos()
        ))
    }

    #[test]
    fn writes_at_0600_and_replaces_content() {
        let path = tmp("w");
        write_atomic(&path, b"first").unwrap();
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        write_atomic(&path, b"second").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"second");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn refuses_to_follow_a_preplanted_symlink() {
        let path = tmp("sym");
        let victim = tmp("victim");
        std::os::unix::fs::symlink(&victim, &path).unwrap();
        // The write must replace the symlink itself, NOT write through it.
        write_atomic(&path, b"over the link").unwrap();
        assert!(
            !std::fs::symlink_metadata(&path)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert!(!victim.exists(), "symlink target must be untouched");
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(&victim);
    }

    #[test]
    fn random_hex_has_expected_length_and_chars() {
        let s = random_hex(4).unwrap();
        assert_eq!(s.len(), 8);
        assert!(s.bytes().all(|b| b.is_ascii_hexdigit()));
    }
}
