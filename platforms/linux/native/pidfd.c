/* Linux-only ABI bridge. Syscall numbers come from the build host's headers. */
#define _GNU_SOURCE
#include <stddef.h>
#include <sys/syscall.h>
#include <unistd.h>

#if !defined(__linux__) || !defined(SYS_pidfd_open) || !defined(SYS_pidfd_send_signal)
#error "Linux headers with pidfd syscall definitions are required"
#endif

int remchat_pidfd_abi(void)
{
    return 1;
}

int remchat_pidfd_open(int pid)
{
    return (int) syscall(SYS_pidfd_open, pid, 0U);
}

int remchat_pidfd_send_signal(int descriptor, int sig)
{
    return (int) syscall(SYS_pidfd_send_signal, descriptor, sig, NULL, 0U);
}
