/*
 * LD_PRELOAD guard: log follow-through open/chmod/chown of a path whose
 * final component is a symbolic link (SYNC.md: never open/chmod/chown the
 * symlink target). Inherited by child processes, unlike interpreter hooks.
 *
 * Passthrough uses syscall(2) so interposed symbols never recurse through
 * dlsym-resolved libc entry points (which can SEGV during early load).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif
#ifndef AT_SYMLINK_NOFOLLOW
#define AT_SYMLINK_NOFOLLOW 0x100
#endif
#ifndef O_TMPFILE
#define O_TMPFILE 0
#endif

#ifdef SYS_newfstatat
#define FSTATAT_NR SYS_newfstatat
#else
#define FSTATAT_NR SYS_fstatat
#endif

static int guard_ready;

static void init_guard(void) __attribute__((constructor));
static void init_guard(void) { guard_ready = 1; }

static int is_symlink_at(int dirfd, const char *path) {
  struct stat st;
  if (path == NULL)
    return 0;
  if (syscall(FSTATAT_NR, dirfd, path, &st, AT_SYMLINK_NOFOLLOW) != 0)
    return 0;
  return S_ISLNK(st.st_mode);
}

static void note(const char *kind, const char *path) {
  const char *logpath;
  int fd;
  char buf[2304];
  int n;

  if (!guard_ready)
    return;
  logpath = getenv("PAFS_SYMLINK_OPEN_LOG");
  if (logpath == NULL || logpath[0] == '\0')
    return;
  n = snprintf(buf, sizeof(buf), "%s:%s\n", kind, path ? path : "");
  if (n <= 0)
    return;
  if ((size_t)n >= sizeof(buf))
    n = (int)sizeof(buf) - 1;
  fd = (int)syscall(SYS_openat, AT_FDCWD, logpath, O_WRONLY | O_CREAT | O_APPEND, 0644);
  if (fd < 0)
    return;
  (void)syscall(SYS_write, fd, buf, (size_t)n);
  (void)syscall(SYS_close, fd);
}

static void check_open_at(int dirfd, const char *path, int flags, const char *kind) {
  if (!guard_ready)
    return;
  if ((flags & O_NOFOLLOW) != 0)
    return;
  if (is_symlink_at(dirfd, path))
    note(kind, path);
}

static int openat_common(int dirfd, const char *pathname, int flags, mode_t mode,
                         const char *kind) {
  check_open_at(dirfd, pathname, flags, kind);
  if (flags & (O_CREAT | O_TMPFILE))
    return (int)syscall(SYS_openat, dirfd, pathname, flags, mode);
  return (int)syscall(SYS_openat, dirfd, pathname, flags);
}

int open(const char *pathname, int flags, ...) {
  mode_t mode = 0;
  if (flags & (O_CREAT | O_TMPFILE)) {
    va_list ap;
    va_start(ap, flags);
    mode = (mode_t)va_arg(ap, int);
    va_end(ap);
  }
  return openat_common(AT_FDCWD, pathname, flags, mode, "open");
}

int open64(const char *pathname, int flags, ...) {
  mode_t mode = 0;
  if (flags & (O_CREAT | O_TMPFILE)) {
    va_list ap;
    va_start(ap, flags);
    mode = (mode_t)va_arg(ap, int);
    va_end(ap);
  }
  return openat_common(AT_FDCWD, pathname, flags, mode, "open64");
}

int openat(int dirfd, const char *pathname, int flags, ...) {
  mode_t mode = 0;
  if (flags & (O_CREAT | O_TMPFILE)) {
    va_list ap;
    va_start(ap, flags);
    mode = (mode_t)va_arg(ap, int);
    va_end(ap);
  }
  return openat_common(dirfd, pathname, flags, mode, "openat");
}

int openat64(int dirfd, const char *pathname, int flags, ...) {
  mode_t mode = 0;
  if (flags & (O_CREAT | O_TMPFILE)) {
    va_list ap;
    va_start(ap, flags);
    mode = (mode_t)va_arg(ap, int);
    va_end(ap);
  }
  return openat_common(dirfd, pathname, flags, mode, "openat64");
}

int chmod(const char *pathname, mode_t mode) {
  if (guard_ready && is_symlink_at(AT_FDCWD, pathname))
    note("chmod", pathname);
  return (int)syscall(SYS_fchmodat, AT_FDCWD, pathname, mode, 0);
}

int fchmodat(int dirfd, const char *pathname, mode_t mode, int flags) {
  if (guard_ready && (flags & AT_SYMLINK_NOFOLLOW) == 0 &&
      is_symlink_at(dirfd, pathname))
    note("fchmodat", pathname);
  return (int)syscall(SYS_fchmodat, dirfd, pathname, mode, flags);
}

int chown(const char *pathname, uid_t owner, gid_t group) {
  if (guard_ready && is_symlink_at(AT_FDCWD, pathname))
    note("chown", pathname);
  return (int)syscall(SYS_fchownat, AT_FDCWD, pathname, owner, group, 0);
}

int lchown(const char *pathname, uid_t owner, gid_t group) {
  return (int)syscall(SYS_fchownat, AT_FDCWD, pathname, owner, group,
                      AT_SYMLINK_NOFOLLOW);
}

int fchownat(int dirfd, const char *pathname, uid_t owner, gid_t group, int flags) {
  if (guard_ready && (flags & AT_SYMLINK_NOFOLLOW) == 0 &&
      is_symlink_at(dirfd, pathname))
    note("fchownat", pathname);
  return (int)syscall(SYS_fchownat, dirfd, pathname, owner, group, flags);
}
