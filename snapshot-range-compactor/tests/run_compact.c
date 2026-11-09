#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

#if defined(__x86_64__)
#define NATIVE_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define NATIVE_ARCH AUDIT_ARCH_AARCH64
#else
#error Unsupported verifier architecture
#endif

#define LOAD_FIELD(field) BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, field))
#define EQUAL(value, yes, no) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, value, yes, no)
#define ALLOW BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
#define DENY BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM)

static void fail(const char *operation) {
    perror(operation);
    exit(125);
}

int main(void) {
    struct rlimit files;
    if (getrlimit(RLIMIT_NOFILE, &files)) fail("getrlimit");
    /* Reserve the highest descriptor, then put it beyond the immutable hard
       limit. CLOEXEC consumes it at the first exec; no later open/dup/recv can
       recreate that descriptor. This avoids an instruction-pointer exception
       or a syscall-count assumption in the seccomp policy. */
    rlim_t capacity = files.rlim_max;
    if (capacity == RLIM_INFINITY || capacity > 1048576) capacity = 1048576;
    if (capacity < 64) { errno = EMFILE; fail("descriptor capacity"); }
    files.rlim_cur = files.rlim_max = capacity;
    if (setrlimit(RLIMIT_NOFILE, &files)) fail("set descriptor capacity");
    int source = open("/runtime/compact", O_RDONLY | O_CLOEXEC);
    if (source < 0) fail("open compactor");
    int executable = fcntl(source, F_DUPFD_CLOEXEC, (int)capacity - 1);
    if (executable < 0) fail("reserve executable descriptor");
    close(source);
    files.rlim_cur = files.rlim_max = executable;
    if (setrlimit(RLIMIT_NOFILE, &files)) fail("seal descriptor limit");

    if (chroot("/runtime") || chdir("/")) fail("runtime root");
    gid_t group = 2000;
    if (setgroups(1, &group) || setgid(group) || setuid(2000)) fail("drop privileges");
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) fail("no_new_privs");

    struct sock_filter filter[] = {
        LOAD_FIELD(arch),
        EQUAL(NATIVE_ARCH, 1, 0), DENY,
        LOAD_FIELD(nr),
        /* x32 shares AUDIT_ARCH_X86_64 but uses a separate syscall namespace. */
        BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x40000000U, 0, 1), DENY,
        EQUAL(SYS_execve, 0, 1), DENY,
        EQUAL(SYS_execveat, 0, 4),
        LOAD_FIELD(args[0]), EQUAL((unsigned int)executable, 0, 1), ALLOW, DENY,
        /* Keep the descriptor exception unusable even if a descendant creates
           a user namespace. Queries remain available to the Go runtime. */
#ifdef SYS_setrlimit
        EQUAL(SYS_setrlimit, 0, 4),
        LOAD_FIELD(args[0]), EQUAL(RLIMIT_NOFILE, 0, 1), DENY, ALLOW,
#endif
        EQUAL(SYS_prlimit64, 0, 8),
        LOAD_FIELD(args[1]), EQUAL(RLIMIT_NOFILE, 0, 6),
        LOAD_FIELD(args[2]), EQUAL(0, 0, 3),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[2]) + 4),
        EQUAL(0, 0, 1), ALLOW, DENY,
        ALLOW,
    };
    struct sock_fprog program = {
        .len = sizeof(filter) / sizeof(filter[0]), .filter = filter,
    };
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) fail("execution filter");
    char *arguments[] = {"/compact", NULL};
    extern char **environ;
    syscall(SYS_execveat, executable, "", arguments, environ, AT_EMPTY_PATH);
    fail("start compactor");
}
