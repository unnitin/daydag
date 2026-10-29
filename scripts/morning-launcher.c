// The faceless app behind the scheduled morning.
//
// launchd cannot run scripts/morning.sh itself: macOS privacy protection gives
// a bare /bin/zsh no access to ~/Documents (the repo) or iCloud Drive (the
// vault), and the grant that unlocks them is keyed to the process macOS holds
// RESPONSIBLE for the access. The terminal launch that preceded this (2b377cf)
// borrowed the terminal app's grant, at the price of a window: the terminal
// spawned no shell while the screen was locked (2026-10-22, 10/23 and 10/28 all
// ran hours late) and closing the window killed the run.
//
// This is the executable of "DayDAG Morning.app", which scripts/install-morning.sh
// builds into ~/.local/state/daydag. It runs `/bin/zsh -l <REPO>/scripts/morning.sh
// <args>` as a child and waits, so the app - not zsh - is what macOS attributes
// the file access to. Granted once, at install (the --probe run), Documents and
// iCloud Drive stay granted to the app, and every morning after runs with no
// window and no prompt.
//
// It must not exec(): exec would turn the process back into /bin/zsh and the
// grant would not apply. REPO is read from ~/.local/state/daydag/morning.env at
// run time, so a path change needs no rebuild - a rebuild changes the binary's
// hash, which is the identity the grant is tied to, and asks again.
//
// The child's stdin is /dev/null (claude -p must never wait on a terminal) and
// its stdout/stderr go to runs/launchd.log, next to the gate's own lines.
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

static const char *home(void) {
    const char *h = getenv("HOME");
    if (h && *h) return h;
    struct passwd *pw = getpwuid(getuid());
    return pw ? pw->pw_dir : "/";
}

static void stamp(FILE *f) {
    char buf[64];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(buf, sizeof buf, "%F %T %Z", &tm);
    fprintf(f, "== %s launcher: ", buf);
}

/* REPO=... from morning.env. The installer writes it with zsh's %q, so a
   backslash escapes the character after it (a space in the path, say). */
static int read_repo(const char *env_path, char *out, size_t cap) {
    FILE *f = fopen(env_path, "r");
    if (!f) return -1;
    char line[4096];
    int found = -1;
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, "REPO=", 5) != 0) continue;
        const char *p = line + 5;
        size_t n = 0;
        while (*p && *p != '\n' && n + 1 < cap) {
            if (*p == '\\' && p[1]) p++;
            out[n++] = *p++;
        }
        out[n] = '\0';
        found = 0;
        break;
    }
    fclose(f);
    return found;
}

int main(int argc, char **argv) {
    const char *h = home();
    char state[1024], env_path[1200], log_path[1200], repo[2048], script[2600];
    snprintf(state, sizeof state, "%s/.local/state/daydag", h);
    snprintf(env_path, sizeof env_path, "%s/morning.env", state);
    snprintf(log_path, sizeof log_path, "%s/runs/launchd.log", state);

    FILE *log = fopen(log_path, "a");
    if (!log) log = stderr;
    setvbuf(log, NULL, _IOLBF, 0);

    if (read_repo(env_path, repo, sizeof repo) != 0 || !*repo) {
        stamp(log);
        fprintf(log, "no REPO in %s - run scripts/install-morning.sh\n", env_path);
        return 1;
    }
    snprintf(script, sizeof script, "%s/scripts/morning.sh", repo);

    /* zsh -l <script> <args...>: a login shell, so PATH is his, not launchd's. */
    char **args = calloc((size_t)argc + 3, sizeof *args);
    if (!args) return 1;
    int n = 0;
    args[n++] = "zsh";
    args[n++] = "-l";
    args[n++] = script;
    for (int i = 1; i < argc; i++) args[n++] = argv[i];
    args[n] = NULL;

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_addopen(&fa, 1, log_path, O_WRONLY | O_APPEND | O_CREAT, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);

    int rc = posix_spawn(&child, "/bin/zsh", &fa, NULL, args, environ);
    posix_spawn_file_actions_destroy(&fa);
    if (rc != 0) {
        stamp(log);
        fprintf(log, "could not start %s: %s\n", script, strerror(rc));
        return 1;
    }
    stamp(log);
    fprintf(log, "started %s%s%s (pid %d)\n", script, argc > 1 ? " " : "",
            argc > 1 ? argv[1] : "", (int)child);

    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno == EINTR) continue;
        stamp(log);
        fprintf(log, "waitpid: %s\n", strerror(errno));
        return 1;
    }
    int code = WIFEXITED(status) ? WEXITSTATUS(status)
             : 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    stamp(log);
    fprintf(log, "%s exited %d\n", script, code);
    return code;
}
