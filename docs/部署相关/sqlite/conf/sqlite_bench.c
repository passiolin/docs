/*
 * sqlite_bench.c - SQLite hardware sizing benchmark
 *
 * Data shape: 1000 devices x 365 days x 5 metrics = 1,825,000 rows.
 * Phases: insert / point / range / agg / conc / idle / verify
 *
 * Every phase runs in a fresh process so VmHWM and sqlite3_memory_used()
 * describe that phase only. Output is one RESULT line per run, key=value.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <inttypes.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sqlite3.h>

#define N_DEV      1000
#define N_DAY      365
#define N_METRIC   5
#define MAX_METRIC 5
#define TS_BASE    1735689600LL                 /* 2025-01-01T00:00:00Z */

static int g_metrics = MAX_METRIC;   /* 1 / 3 / 5: data volume gradient */
#define DAY_SEC    86400LL

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static long proc_status_kb(const char *key)
{
    FILE *f = fopen("/proc/self/status", "r");
    char line[256];
    long v = -1;
    if (!f) return -1;
    while (fgets(line, sizeof line, f)) {
        if (!strncmp(line, key, strlen(key))) {
            sscanf(line + strlen(key), ": %ld", &v);
            break;
        }
    }
    fclose(f);
    return v;
}

static long meminfo_kb(const char *key)
{
    FILE *f = fopen("/proc/meminfo", "r");
    char line[256];
    long v = -1;
    if (!f) return -1;
    while (fgets(line, sizeof line, f)) {
        if (!strncmp(line, key, strlen(key))) {
            sscanf(line + strlen(key), ": %ld", &v);
            break;
        }
    }
    fclose(f);
    return v;
}

static int64_t sqlite_mem_used(void)
{
    return sqlite3_memory_used();
}

static int64_t sqlite_mem_high(void)
{
    return sqlite3_memory_highwater(0);
}

typedef struct {
    unsigned long long user, nice, system, idle, iowait, irq, softirq, steal;
} SysCpu;

static void read_sys_cpu(SysCpu *c)
{
    FILE *f = fopen("/proc/stat", "r");
    char line[512];
    memset(c, 0, sizeof *c);
    if (f) {
        if (fgets(line, sizeof line, f))
            sscanf(line, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
                   &c->user, &c->nice, &c->system, &c->idle,
                   &c->iowait, &c->irq, &c->softirq, &c->steal);
        fclose(f);
    }
}

static double sys_busy_pct(const SysCpu *a, const SysCpu *b)
{
    unsigned long long busy_a = a->user + a->nice + a->system + a->irq + a->softirq + a->steal;
    unsigned long long busy_b = b->user + b->nice + b->system + b->irq + b->softirq + b->steal;
    unsigned long long idle_a = a->idle + a->iowait;
    unsigned long long idle_b = b->idle + b->iowait;
    double dbusy = (double)(busy_b - busy_a), didle = (double)(idle_b - idle_a);
    if (dbusy + didle <= 0) return 0.0;
    return 100.0 * dbusy / (dbusy + didle);
}

static void cpu_times(double *user, double *sys)
{
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    *user = (double)ru.ru_utime.tv_sec + ru.ru_utime.tv_usec / 1e6;
    *sys  = (double)ru.ru_stime.tv_sec + ru.ru_stime.tv_usec / 1e6;
}

static int64_t file_bytes(const char *path)
{
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    return (int64_t)st.st_size;
}

static sqlite3 *open_db(const char *path, int cache_kb, int wal)
{
    sqlite3 *db = NULL;
    int rc = sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "open %s: %s\n", path, sqlite3_errmsg(db));
        exit(2);
    }
    sqlite3_busy_timeout(db, 5000);
    if (cache_kb > 0) {
        char sql[96];
        snprintf(sql, sizeof sql, "PRAGMA cache_size=-%d;", cache_kb);
        sqlite3_exec(db, sql, NULL, NULL, NULL);
    }
    if (wal) {
        sqlite3_exec(db, "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;", NULL, NULL, NULL);
    }
    return db;
}

static void exec_or_die(sqlite3 *db, const char *sql)
{
    char *err = NULL;
    if (sqlite3_exec(db, sql, NULL, NULL, &err) != SQLITE_OK) {
        fprintf(stderr, "sql failed: %s\n%s\n", sql, err ? err : "");
        sqlite3_free(err);
        exit(2);
    }
}

static int64_t pragma_int(sqlite3 *db, const char *name)
{
    char sql[96];
    sqlite3_stmt *st = NULL;
    int64_t v = -1;
    snprintf(sql, sizeof sql, "PRAGMA %s;", name);
    if (sqlite3_prepare_v2(db, sql, -1, &st, NULL) == SQLITE_OK && sqlite3_step(st) == SQLITE_ROW)
        v = sqlite3_column_int64(st, 0);
    sqlite3_finalize(st);
    return v;
}

static uint64_t mix64(uint64_t x)
{
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}

static double value_of(int device, int day, int metric)
{
    uint64_t h = mix64((uint64_t)device * 1000000ULL + (uint64_t)day * 100ULL + (uint64_t)metric);
    return (double)(h % 1000000ULL) / 1000.0;
}

static uint64_t rng_state = 88172645463325252ULL;
static uint64_t xrand(void)
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}

/* ---------------------------------------------------------------- insert */

static int cmd_insert(const char *dbpath, int cache_kb, int wal, int batch, int metrics)
{
    g_metrics = metrics;
    char side[4096];
    snprintf(side, sizeof side, "%s-journal", dbpath); unlink(side);
    snprintf(side, sizeof side, "%s-wal", dbpath);     unlink(side);
    snprintf(side, sizeof side, "%s-shm", dbpath);     unlink(side);
    unlink(dbpath);

    sqlite3 *db = open_db(dbpath, cache_kb, wal);
    exec_or_die(db,
        "CREATE TABLE metrics("
        " device_id INTEGER NOT NULL,"
        " ts        INTEGER NOT NULL,"
        " metric_id INTEGER NOT NULL,"
        " value     REAL NOT NULL);"
        "CREATE INDEX idx_dev_ts ON metrics(device_id, ts);"
        "CREATE INDEX idx_metric_ts ON metrics(metric_id, ts);");

    sqlite3_stmt *ins = NULL;
    sqlite3_prepare_v2(db,
        "INSERT INTO metrics(device_id, ts, metric_id, value) VALUES(?,?,?,?)",
        -1, &ins, NULL);

    SysCpu c0, c1;
    double u0, s0, u1, s1;
    double t0, t1;
    read_sys_cpu(&c0);
    cpu_times(&u0, &s0);
    t0 = now_s();

    int64_t n = 0;
    exec_or_die(db, "BEGIN");
    for (int day = 0; day < N_DAY; day++) {
        int64_t ts = TS_BASE + (int64_t)day * DAY_SEC;
        for (int dev = 1; dev <= N_DEV; dev++) {
            for (int m = 1; m <= g_metrics; m++) {
                sqlite3_bind_int(ins, 1, dev);
                sqlite3_bind_int64(ins, 2, ts);
                sqlite3_bind_int(ins, 3, m);
                sqlite3_bind_double(ins, 4, value_of(dev, day, m));
                if (sqlite3_step(ins) != SQLITE_DONE) {
                    fprintf(stderr, "insert failed: %s\n", sqlite3_errmsg(db));
                    exit(2);
                }
                sqlite3_reset(ins);
                n++;
                if (n % batch == 0) {
                    exec_or_die(db, "COMMIT");
                    if (n % (batch * 20) == 0)
                        fprintf(stderr, "  inserted %" PRId64 " rows, %.1fs\n", n, now_s() - t0);
                    exec_or_die(db, "BEGIN");
                }
            }
        }
    }
    exec_or_die(db, "COMMIT");
    sqlite3_finalize(ins);

    t1 = now_s();
    cpu_times(&u1, &s1);
    read_sys_cpu(&c1);

    int64_t pages = pragma_int(db, "page_count");
    int64_t psize = pragma_int(db, "page_size");
    int64_t used  = sqlite_mem_used();
    int64_t high  = sqlite_mem_high();
    int64_t dbbytes = file_bytes(dbpath);
    sqlite3_close(db);

    printf("RESULT mode=insert rows=%" PRId64 " metrics=%d batch=%d wall_s=%.3f cpu_user_s=%.3f "
           "cpu_sys_s=%.3f cpu_pct=%.1f vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " sqlite_mem_high=%" PRId64 " db_bytes=%" PRId64 " page_size=%" PRId64
           " pages=%" PRId64 " cache_kb=%d wal=%d sys_busy_pct=%.1f\n",
           n, g_metrics, batch, t1 - t0, u1 - u0, s1 - s0,
           100.0 * ((u1 - u0) + (s1 - s0)) / (t1 - t0),
           proc_status_kb("VmHWM"), used, high, dbbytes, psize, pages,
           cache_kb, wal, sys_busy_pct(&c0, &c1));
    return 0;
}

/* ------------------------------------------------------------ read phases */

static int cmd_point(const char *dbpath, int iters, int cache_kb, int wal, int metrics)
{
    sqlite3 *db = open_db(dbpath, cache_kb, wal);
    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db,
        "SELECT value FROM metrics WHERE device_id=? AND ts=? AND metric_id=?",
        -1, &st, NULL);
    if (!st) { fprintf(stderr, "prepare: %s\n", sqlite3_errmsg(db)); return 2; }

    for (int i = 0; i < 1000; i++) {
        int dev = 1 + (int)(xrand() % N_DEV);
        int day = (int)(xrand() % N_DAY);
        int met = 1 + (int)(xrand() % metrics);
        sqlite3_bind_int(st, 1, dev);
        sqlite3_bind_int64(st, 2, TS_BASE + (int64_t)day * DAY_SEC);
        sqlite3_bind_int(st, 3, met);
        sqlite3_step(st);
        sqlite3_reset(st);
    }

    SysCpu c0, c1;
    double u0, s0, u1, s1, t0, t1, sum = 0;
    read_sys_cpu(&c0); cpu_times(&u0, &s0); t0 = now_s();

    for (int i = 0; i < iters; i++) {
        int dev = 1 + (int)(xrand() % N_DEV);
        int day = (int)(xrand() % N_DAY);
        int met = 1 + (int)(xrand() % metrics);
        sqlite3_bind_int(st, 1, dev);
        sqlite3_bind_int64(st, 2, TS_BASE + (int64_t)day * DAY_SEC);
        sqlite3_bind_int(st, 3, met);
        if (sqlite3_step(st) == SQLITE_ROW) sum += sqlite3_column_double(st, 0);
        sqlite3_reset(st);
    }

    t1 = now_s(); cpu_times(&u1, &s1); read_sys_cpu(&c1);
    sqlite3_finalize(st);
    int64_t used = sqlite_mem_used(), high = sqlite_mem_high();
    sqlite3_close(db);

    printf("RESULT mode=point iters=%d wall_s=%.3f qps=%.0f avg_us=%.2f cpu_user_s=%.3f "
           "cpu_sys_s=%.3f cpu_pct=%.1f vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " sqlite_mem_high=%" PRId64 " cache_kb=%d wal=%d sys_busy_pct=%.1f sum=%.1f\n",
           iters, t1 - t0, iters / (t1 - t0), 1e6 * (t1 - t0) / iters,
           u1 - u0, s1 - s0, 100.0 * ((u1 - u0) + (s1 - s0)) / (t1 - t0),
           proc_status_kb("VmHWM"), used, high, cache_kb, wal, sys_busy_pct(&c0, &c1), sum);
    return 0;
}

static int cmd_range(const char *dbpath, int iters, int cache_kb, int wal)
{
    sqlite3 *db = open_db(dbpath, cache_kb, wal);
    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db,
        "SELECT ts, metric_id, value FROM metrics WHERE device_id=? AND ts BETWEEN ? AND ?",
        -1, &st, NULL);
    if (!st) { fprintf(stderr, "prepare: %s\n", sqlite3_errmsg(db)); return 2; }

    SysCpu c0, c1;
    double u0, s0, u1, s1, t0, t1, sum = 0;
    int64_t got = 0;
    read_sys_cpu(&c0); cpu_times(&u0, &s0); t0 = now_s();

    for (int i = 0; i < iters; i++) {
        int dev = 1 + (int)(xrand() % N_DEV);
        sqlite3_bind_int(st, 1, dev);
        sqlite3_bind_int64(st, 2, TS_BASE);
        sqlite3_bind_int64(st, 3, TS_BASE + (int64_t)(N_DAY - 1) * DAY_SEC);
        while (sqlite3_step(st) == SQLITE_ROW) { sum += sqlite3_column_double(st, 2); got++; }
        sqlite3_reset(st);
    }

    t1 = now_s(); cpu_times(&u1, &s1); read_sys_cpu(&c1);
    sqlite3_finalize(st);
    int64_t used = sqlite_mem_used(), high = sqlite_mem_high();
    sqlite3_close(db);

    printf("RESULT mode=range iters=%d rows_per_q=%" PRId64 " wall_s=%.3f qps=%.0f avg_us=%.2f "
           "cpu_user_s=%.3f cpu_sys_s=%.3f cpu_pct=%.1f vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " sqlite_mem_high=%" PRId64 " cache_kb=%d wal=%d sys_busy_pct=%.1f sum=%.1f\n",
           iters, iters ? got / iters : 0, t1 - t0, iters / (t1 - t0),
           1e6 * (t1 - t0) / iters, u1 - u0, s1 - s0,
           100.0 * ((u1 - u0) + (s1 - s0)) / (t1 - t0), proc_status_kb("VmHWM"),
           used, high, cache_kb, wal, sys_busy_pct(&c0, &c1), sum);
    return 0;
}

static int cmd_agg(const char *dbpath, int iters, int cache_kb, int wal)
{
    sqlite3 *db = open_db(dbpath, cache_kb, wal);
    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db,
        "SELECT metric_id, count(*), avg(value), max(value) FROM metrics "
        "WHERE ts>=? AND ts<? GROUP BY metric_id",
        -1, &st, NULL);
    if (!st) { fprintf(stderr, "prepare: %s\n", sqlite3_errmsg(db)); return 2; }

    SysCpu c0, c1;
    double u0, s0, u1, s1, t0, t1, sum = 0;
    int64_t rows = 0;
    read_sys_cpu(&c0); cpu_times(&u0, &s0); t0 = now_s();

    for (int i = 0; i < iters; i++) {
        int day = (int)(xrand() % (N_DAY - 30));
        sqlite3_bind_int64(st, 1, TS_BASE + (int64_t)day * DAY_SEC);
        sqlite3_bind_int64(st, 2, TS_BASE + (int64_t)(day + 30) * DAY_SEC);
        while (sqlite3_step(st) == SQLITE_ROW) { sum += sqlite3_column_double(st, 2); rows++; }
        sqlite3_reset(st);
    }

    t1 = now_s(); cpu_times(&u1, &s1); read_sys_cpu(&c1);
    sqlite3_finalize(st);
    int64_t used = sqlite_mem_used(), high = sqlite_mem_high();
    sqlite3_close(db);

    printf("RESULT mode=agg iters=%d coverage_days=30 wall_s=%.3f qps=%.1f avg_ms=%.2f "
           "cpu_user_s=%.3f cpu_sys_s=%.3f cpu_pct=%.1f vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " sqlite_mem_high=%" PRId64 " cache_kb=%d wal=%d sys_busy_pct=%.1f sum=%.1f\n",
           iters, t1 - t0, iters / (t1 - t0), 1000.0 * (t1 - t0) / iters,
           u1 - u0, s1 - s0, 100.0 * ((u1 - u0) + (s1 - s0)) / (t1 - t0),
           proc_status_kb("VmHWM"), used, high, cache_kb, wal, sys_busy_pct(&c0, &c1), sum);
    return 0;
}

/* ------------------------------------------------------------ concurrent */

typedef struct {
    const char *dbpath;
    double deadline;
    int tid;
    int64_t point_ops;
    int64_t range_ops;
    int64_t rows_seen;
    int mix_point_pct;
    int metrics;
    double sum;
} Worker;

static void *worker_main(void *arg)
{
    Worker *w = (Worker *)arg;
    sqlite3 *db = open_db(w->dbpath, 0, 0);
    sqlite3_stmt *pt = NULL, *rg = NULL;
    sqlite3_prepare_v2(db,
        "SELECT value FROM metrics WHERE device_id=? AND ts=? AND metric_id=?",
        -1, &pt, NULL);
    sqlite3_prepare_v2(db,
        "SELECT ts, metric_id, value FROM metrics WHERE device_id=? AND ts BETWEEN ? AND ?",
        -1, &rg, NULL);
    uint64_t rs = 88172645463325252ULL ^ ((uint64_t)w->tid * 0x9e3779b97f4a7c15ULL);
    while (now_s() < w->deadline) {
        rs ^= rs << 13;
        rs ^= rs >> 7;
        rs ^= rs << 17;
        uint64_t z = rs;
        int dev = 1 + (int)(z % N_DEV);
        int day = (int)((z >> 20) % N_DAY);
        int met = 1 + (int)((z >> 40) % w->metrics);
        if ((int)((z >> 8) % 100) < w->mix_point_pct) {
            sqlite3_bind_int(pt, 1, dev);
            sqlite3_bind_int64(pt, 2, TS_BASE + (int64_t)day * DAY_SEC);
            sqlite3_bind_int(pt, 3, met);
            if (sqlite3_step(pt) == SQLITE_ROW) w->sum += sqlite3_column_double(pt, 0);
            sqlite3_reset(pt);
            w->point_ops++;
        } else {
            sqlite3_bind_int(rg, 1, dev);
            sqlite3_bind_int64(rg, 2, TS_BASE);
            sqlite3_bind_int64(rg, 3, TS_BASE + (int64_t)(N_DAY - 1) * DAY_SEC);
            while (sqlite3_step(rg) == SQLITE_ROW) { w->sum += sqlite3_column_double(rg, 2); w->rows_seen++; }
            sqlite3_reset(rg);
            w->range_ops++;
        }
    }
    sqlite3_finalize(pt);
    sqlite3_finalize(rg);
    sqlite3_close(db);
    return NULL;
}

static int cmd_conc(const char *dbpath, int threads, int seconds, int mix_point_pct, int metrics)
{
    SysCpu c0, c1;
    double u0, s0, u1, s1, t0, t1;
    pthread_t *th = calloc((size_t)threads, sizeof *th);
    Worker *w = calloc((size_t)threads, sizeof *w);
    if (!th || !w) { fprintf(stderr, "oom\n"); return 2; }

    read_sys_cpu(&c0); cpu_times(&u0, &s0); t0 = now_s();
    double deadline = t0 + seconds;
    for (int i = 0; i < threads; i++) {
        w[i].dbpath = dbpath;
        w[i].deadline = deadline;
        w[i].tid = i;
        w[i].mix_point_pct = mix_point_pct;
        w[i].metrics = metrics;
        pthread_create(&th[i], NULL, worker_main, &w[i]);
    }
    int64_t pts = 0, rgs = 0, rows = 0;
    double sum = 0;
    for (int i = 0; i < threads; i++) {
        pthread_join(th[i], NULL);
        pts += w[i].point_ops; rgs += w[i].range_ops; rows += w[i].rows_seen; sum += w[i].sum;
    }
    t1 = now_s(); cpu_times(&u1, &s1); read_sys_cpu(&c1);

    int64_t ops = pts + rgs;
    printf("RESULT mode=conc threads=%d seconds=%d mix_point_pct=%d metrics=%d wall_s=%.3f ops=%" PRId64
           " point_ops=%" PRId64 " range_ops=%" PRId64 " ops_qps=%.0f rows=%" PRId64
           " cpu_user_s=%.3f cpu_sys_s=%.3f cpu_pct_onecore=%.0f cores_busy=%.2f "
           "vmhwm_kb=%ld sqlite_mem_used=%" PRId64 " sys_busy_pct=%.1f sum=%.1f\n",
           threads, seconds, mix_point_pct, metrics, t1 - t0, ops, pts, rgs, ops / (t1 - t0), rows,
           u1 - u0, s1 - s0, 100.0 * ((u1 - u0) + (s1 - s0)) / (t1 - t0),
           ((u1 - u0) + (s1 - s0)) / (t1 - t0), proc_status_kb("VmHWM"),
           sqlite_mem_used(), sys_busy_pct(&c0, &c1), sum);
    free(th); free(w);
    return 0;
}

/* ---------------------------------------------------------------- idle */

static int cmd_idle(const char *dbpath, int cache_kb, int sleep_s, int wal)
{
    long avail0 = meminfo_kb("MemAvailable");
    long cached0 = meminfo_kb("Cached");

    sqlite3 *db = open_db(dbpath, cache_kb, wal);

    printf("IDLE stage=opened_no_query rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " cache_size=%" PRId64 " page_count=%" PRId64 " page_size=%" PRId64
           " journal_mode=%s\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
           pragma_int(db, "cache_size"), pragma_int(db, "page_count"),
           pragma_int(db, "page_size"),
           wal ? "wal" : "default(rollback)");
    fflush(stdout);
    sleep(2);
    printf("IDLE stage=after_2s_idle rss_kb=%ld sqlite_mem_used=%" PRId64
           " mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), sqlite_mem_used(),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
    fflush(stdout);

    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db,
        "SELECT value FROM metrics WHERE device_id=? AND ts=? AND metric_id=?",
        -1, &st, NULL);
    for (int i = 0; i < 1000; i++) {
        sqlite3_bind_int(st, 1, 1 + i % N_DEV);
        sqlite3_bind_int64(st, 2, TS_BASE + (int64_t)(i % N_DAY) * DAY_SEC);
        sqlite3_bind_int(st, 3, 1 + i % N_METRIC);
        sqlite3_step(st);
        sqlite3_reset(st);
    }
    sqlite3_finalize(st);
    printf("IDLE stage=after_1000_point_queries rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
    fflush(stdout);

    sqlite3_stmt *ag = NULL;
    sqlite3_prepare_v2(db,
        "SELECT metric_id, count(*), avg(value), max(value) FROM metrics "
        "WHERE ts>=? AND ts<? GROUP BY metric_id", -1, &ag, NULL);
    sqlite3_bind_int64(ag, 1, TS_BASE);
    sqlite3_bind_int64(ag, 2, TS_BASE + 30 * DAY_SEC);
    while (sqlite3_step(ag) == SQLITE_ROW) { }
    sqlite3_finalize(ag);
    printf("IDLE stage=after_30d_scan rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
    fflush(stdout);

    sqlite3_stmt *fs = NULL;
    sqlite3_prepare_v2(db, "SELECT count(*), sum(value) FROM metrics", -1, &fs, NULL);
    while (sqlite3_step(fs) == SQLITE_ROW) { }
    sqlite3_finalize(fs);
    printf("IDLE stage=after_full_table_scan rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
    fflush(stdout);

    int step = sleep_s / 3 > 0 ? sleep_s / 3 : 1;
    int elapsed = 0;
    while (elapsed < sleep_s) {
        sleep((unsigned)step);
        elapsed += step;
        printf("IDLE stage=idle_after_query t_s=%d rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
               " mem_available_kb=%ld cached_kb=%ld\n",
               elapsed, proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
               meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
        fflush(stdout);
    }

    exec_or_die(db, "PRAGMA shrink_memory;");
    printf("IDLE stage=after_shrink_memory rss_kb=%ld vmhwm_kb=%ld sqlite_mem_used=%" PRId64
           " mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"), sqlite_mem_used(),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));

    sqlite3_close(db);
    printf("IDLE stage=closed rss_kb=%ld vmhwm_kb=%ld mem_available_kb=%ld cached_kb=%ld\n",
           proc_status_kb("VmRSS"), proc_status_kb("VmHWM"),
           meminfo_kb("MemAvailable"), meminfo_kb("Cached"));
    printf("RESULT mode=idle cache_kb=%d wal=%d sleep_s=%d mem_available_start_kb=%ld "
           "mem_available_end_kb=%ld cached_start_kb=%ld cached_end_kb=%ld\n",
           cache_kb, wal, sleep_s, avail0, meminfo_kb("MemAvailable"), cached0,
           meminfo_kb("Cached"));
    return 0;
}

/* -------------------------------------------------------------- verify */

static int cmd_verify(const char *dbpath)
{
    sqlite3 *db = open_db(dbpath, 0, 0);
    sqlite3_stmt *st = NULL;
    sqlite3_prepare_v2(db,
        "SELECT count(*), count(DISTINCT device_id), min(ts), max(ts), count(DISTINCT metric_id), min(metric_id) "
        "FROM metrics", -1, &st, NULL);
    sqlite3_step(st);
    printf("RESULT mode=verify rows=%" PRId64 " devices=%" PRId64 " min_ts=%" PRId64
           " max_ts=%" PRId64 " metrics=%" PRId64 " min_metric=%" PRId64
           " db_bytes=%" PRId64 " page_size=%" PRId64 " pages=%" PRId64
           " freelist=%" PRId64 " journal_mode=%s\n",
           (int64_t)sqlite3_column_int64(st, 0), (int64_t)sqlite3_column_int64(st, 1),
           (int64_t)sqlite3_column_int64(st, 2), (int64_t)sqlite3_column_int64(st, 3),
           (int64_t)sqlite3_column_int64(st, 4), (int64_t)sqlite3_column_int64(st, 5),
           file_bytes(dbpath), pragma_int(db, "page_size"), pragma_int(db, "page_count"),
           pragma_int(db, "freelist_count"), "default");
    sqlite3_finalize(st);
    sqlite3_close(db);
    return 0;
}

static void usage(const char *p)
{
    fprintf(stderr,
        "usage:\n"
        "  %s insert <db> [cache_kb] [wal 0|1] [batch] [metrics]\n"
        "  %s verify <db>\n"
        "  %s point  <db> [iters] [cache_kb] [wal] [metrics]\n"
        "  %s range  <db> [iters] [cache_kb] [wal]\n"
        "  %s agg    <db> [iters] [cache_kb] [wal]\n"
        "  %s conc   <db> [threads] [seconds] [mix_point_pct] [metrics]\n"
        "  %s idle   <db> [cache_kb] [sleep_s] [wal]\n", p, p, p, p, p, p, p);
}

int main(int argc, char **argv)
{
    if (argc < 3) { usage(argv[0]); return 1; }
    const char *mode = argv[1], *db = argv[2];

    if (!strcmp(mode, "insert"))
        return cmd_insert(db,
            argc > 3 ? atoi(argv[3]) : 0,
            argc > 4 ? atoi(argv[4]) : 0,
            argc > 5 ? atoi(argv[5]) : 5000,
            argc > 6 ? atoi(argv[6]) : MAX_METRIC);
    if (!strcmp(mode, "verify")) return cmd_verify(db);
    if (!strcmp(mode, "point"))
        return cmd_point(db,
            argc > 3 ? atoi(argv[3]) : 200000,
            argc > 4 ? atoi(argv[4]) : 0,
            argc > 5 ? atoi(argv[5]) : 0,
            argc > 6 ? atoi(argv[6]) : MAX_METRIC);
    if (!strcmp(mode, "range"))
        return cmd_range(db,
            argc > 3 ? atoi(argv[3]) : 3000,
            argc > 4 ? atoi(argv[4]) : 0,
            argc > 5 ? atoi(argv[5]) : 0);
    if (!strcmp(mode, "agg"))
        return cmd_agg(db,
            argc > 3 ? atoi(argv[3]) : 200,
            argc > 4 ? atoi(argv[4]) : 0,
            argc > 5 ? atoi(argv[5]) : 0);
    if (!strcmp(mode, "conc"))
        return cmd_conc(db,
            argc > 3 ? atoi(argv[3]) : (int)sysconf(_SC_NPROCESSORS_ONLN),
            argc > 4 ? atoi(argv[4]) : 20,
            argc > 5 ? atoi(argv[5]) : 95,
            argc > 6 ? atoi(argv[6]) : MAX_METRIC);
    if (!strcmp(mode, "idle"))
        return cmd_idle(db,
            argc > 3 ? atoi(argv[3]) : 0,
            argc > 4 ? atoi(argv[4]) : 30,
            argc > 5 ? atoi(argv[5]) : 0);
    usage(argv[0]);
    return 1;
}
