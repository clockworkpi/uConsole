"""Native Linux watchdog keeper source; never run against the host watchdog."""

SOURCE = r'''
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

struct keeper_ops {
    double (*now)(void *);
    int (*ping)(void *);
    int (*ready)(void *);
    int (*wait)(void *);
    /* Optional authenticated lease source. 0 means no new request, negative
     * means failure, positive means requested seconds from this sample. */
    double (*renew)(void *);
};

/* Renewal is deliberately not connected to production yet. A transport must
 * authenticate/fence requests and retain recovery boot selection through any
 * destructive restore before it may enable this hook. Each renewal grants at
 * most five minutes; total session lifetime remains capped at 24 hours. */
static int keep(void *ctx, const struct keeper_ops *ops, double duration)
{
    double start = ops->now(ctx), previous = start;
    double deadline = start + duration;
    int announced = 0;
    if (!isfinite(start) || start < 0 || !isfinite(duration) || duration <= 0 || duration > 300) return 1;
    for (;;) {
        double now = ops->now(ctx);
        /* Expiry precedes renewal: a late request cannot resurrect a lease. */
        if (!isfinite(now) || now < previous || now >= deadline || now - start >= 86400) return 1;
        previous = now;
        if (ops->renew) {
            double extension = ops->renew(ctx);
            if (!isfinite(extension) || extension < 0 || extension > 300) return 1;
            if (extension > 0 && now + extension > deadline) {
                deadline = now + extension;
                if (deadline > start + 86400) deadline = start + 86400;
            }
        }
        if (ops->ping(ctx)) return 1;
        if (!announced) {
            if (ops->ready(ctx)) return 1;
            announced = 1;
        }
        if (ops->wait(ctx)) return 1;
    }
}

#ifndef FORGE_WATCHDOG_TEST
struct device { int fd; int timeout; };
static double monotonic_now(void *ctx)
{
    struct timespec t;
    (void)ctx;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return t.tv_sec + t.tv_nsec / 1e9;
}
static int ping_device(void *ctx)
{
    return ioctl(((struct device *)ctx)->fd, WDIOC_KEEPALIVE, 0);
}
static int announce(void *ctx)
{
    struct device *d = ctx;
    int fd = open("/run/forge-watchdog.ready", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0600);
    if (fd < 0) return -1;
    int result = dprintf(fd, "pid=%ld timeout=%d maximum_lifetime=300\n", (long)getpid(), d->timeout);
    int synced = fsync(fd);
    int closed = close(fd);
    return result < 0 || synced || closed;
}
static int wait_interval(void *ctx)
{
    struct timespec t = {2, 0};
    (void)ctx;
    return nanosleep(&t, NULL);
}
int main(void)
{
    char *line = NULL;
    size_t size = 0;
    int recovery = 0, requested = 0;
    FILE *cmd = fopen("/proc/cmdline", "r");
    if (!cmd || geteuid() != 0) return 1;
    if (getline(&line, &size, cmd) < 0) return 1;
    fclose(cmd);
    for (char *p = strtok(line, " \n\t"); p; p = strtok(NULL, " \n\t")) {
        if (!strcmp(p, "uconsole.recovery=1")) recovery = 1;
        if (!strcmp(p, "uconsole.recovery_watchdog=1")) requested = 1;
    }
    free(line);
    if (!recovery || !requested) return 1;
    struct device d = {.fd = open("/dev/watchdog0", O_WRONLY|O_CLOEXEC|O_NOFOLLOW), .timeout = 15};
    if (d.fd < 0) return 1;
    struct watchdog_info info = {0};
    if (ioctl(d.fd, WDIOC_GETSUPPORT, &info) ||
        strncmp((char *)info.identity, "Broadcom BCM2835 Watchdog timer", sizeof(info.identity)) ||
        !(info.options & WDIOF_MAGICCLOSE) || !(info.options & WDIOF_SETTIMEOUT) ||
        ioctl(d.fd, WDIOC_SETTIMEOUT, &d.timeout) ||
        ioctl(d.fd, WDIOC_GETTIMEOUT, &d.timeout) || d.timeout < 10 || d.timeout > 60) {
        close(d.fd);
        return 1;
    }
    const struct keeper_ops ops = {monotonic_now, ping_device, announce, wait_interval, NULL};
    int result = keep(&d, &ops, 300);
    /* Never send 'V' or WDIOS_DISABLECARD: expiry/crash must leave it armed. */
    close(d.fd);
    return result;
}
#endif
'''
