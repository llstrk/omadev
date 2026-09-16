/* Experimental host-Wayland keyboard gate. Loaded only into the disposable
 * Hyprland under test. A lease file contains: generation monotonic_deadline_ns.
 * No lease, expired lease, focus leave, or a new generation clears forwarded
 * keys. Keys held before capture must be released before delivery can begin.
 * Virtual keyboard protocol input does not pass through these client hooks.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <wayland-server-core.h>

#define KEY_COUNT 768
struct keyboard {
  struct wl_proxy *proxy;
  const struct wl_keyboard_listener *original;
  void *data;
  bool physical[KEY_COUNT], forwarded[KEY_COUNT];
  bool entered, open;
  uint64_t generation, revoked;
  uint32_t serial, time;
  struct keyboard *next;
};
static struct keyboard *keyboards;
static char *lease_path;
static struct wl_event_source *timer;
static bool initialized;

static void initialize(void) {
  if (initialized) return;
  initialized = true;
  const char *path = getenv("OMADEV_KEYBOARD_GATE");
  if (!path || !*path) return;
  lease_path = strdup(path);
  if (!lease_path) _exit(125);
  /* Do not gate the apps/virtual keyboards launched by this compositor. */
  const char *original = getenv("OMADEV_ORIGINAL_LD_PRELOAD");
  if (original && *original) setenv("LD_PRELOAD", original, 1);
  else unsetenv("LD_PRELOAD");
  unsetenv("OMADEV_ORIGINAL_LD_PRELOAD");
  unsetenv("OMADEV_KEYBOARD_GATE");
}
static uint64_t now_ns(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}
static uint64_t authorization(void) {
  if (!lease_path) return 0;
  int fd = open(lease_path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) return 0;
  char buffer[128];
  ssize_t n = read(fd, buffer, sizeof(buffer)-1);
  close(fd);
  if (n <= 0) return 0;
  buffer[n] = 0;
  unsigned long long generation=0, deadline=0;
  char trailing;
  if (sscanf(buffer, "%llu %llu %c", &generation, &deadline, &trailing) != 2) return 0;
  return generation && deadline > now_ns() ? generation : 0;
}
static void release_forwarded(struct keyboard *k) {
  for (unsigned code=0; code<KEY_COUNT; ++code) {
    if (!k->forwarded[code]) continue;
    k->forwarded[code] = false;
    if (k->original->key)
      k->original->key(k->data, (struct wl_keyboard *)k->proxy, k->serial,
                       (uint32_t)(now_ns()/1000000ULL), code, WL_KEYBOARD_KEY_STATE_RELEASED);
  }
  if (k->open && k->original->modifiers)
    k->original->modifiers(k->data, (struct wl_keyboard *)k->proxy, k->serial, 0,0,0,0);
  k->open = false;
}
static bool held(struct keyboard *k) {
  for (unsigned i=0;i<KEY_COUNT;++i) if (k->physical[i]) return true;
  return false;
}
static bool refresh(struct keyboard *k) {
  uint64_t generation = authorization();
  if (generation != k->generation) {
    if (!generation && k->generation) k->revoked = k->generation;
    release_forwarded(k);
    k->generation = generation;
  }
  if (!generation || generation == k->revoked || !k->entered) {
    release_forwarded(k);
    return false;
  }
  /* Once open, deliberate holds stay held. Only entry waits for all releases. */
  if (!k->open && !held(k)) k->open = true;
  return k->open;
}
static int tick(void *data) {
  for (struct keyboard *k=keyboards;k;k=k->next) refresh(k);
  if (timer) wl_event_source_timer_update(timer, 20);
  return 0;
}
struct wl_display *wl_display_create(void) {
  static struct wl_display *(*real_create)(void);
  if (!real_create) real_create = dlsym(RTLD_NEXT, "wl_display_create");
  initialize();
  struct wl_display *display = real_create();
  if (lease_path && display && !timer) {
    timer=wl_event_loop_add_timer(wl_display_get_event_loop(display),tick,NULL);
    if (!timer) { fprintf(stderr,"omadev gate: cannot install watchdog\n"); _exit(125); }
    wl_event_source_timer_update(timer,20);
  }
  return display;
}
static void keymap(void *data,struct wl_keyboard *p,uint32_t format,int32_t fd,uint32_t size) {
  struct keyboard *k=data;
  if (k->original->keymap) k->original->keymap(k->data,p,format,fd,size);
  else close(fd);
}
static void enter(void *data,struct wl_keyboard *p,uint32_t serial,struct wl_surface *surface,struct wl_array *keys) {
  struct keyboard *k=data;
  release_forwarded(k);
  memset(k->physical,0,sizeof k->physical);
  uint32_t *code;
  wl_array_for_each(code,keys) if (*code<KEY_COUNT) k->physical[*code]=true;
  k->entered=true; k->serial=serial;
  refresh(k);
  struct wl_array empty; wl_array_init(&empty);
  if (k->original->enter) k->original->enter(k->data,p,serial,surface,&empty);
}
static void leave(void *data,struct wl_keyboard *p,uint32_t serial,struct wl_surface *surface) {
  struct keyboard *k=data;
  k->serial=serial;
  k->revoked=authorization();
  release_forwarded(k);
  k->entered=false;
  memset(k->physical,0,sizeof k->physical);
  if (k->original->leave) k->original->leave(k->data,p,serial,surface);
}
static void key(void *data,struct wl_keyboard *p,uint32_t serial,uint32_t time,uint32_t code,uint32_t state) {
  struct keyboard *k=data;
  k->serial=serial; k->time=time;
  bool allow=refresh(k); /* Check before updating physical: final release stays swallowed. */
  if (code>=KEY_COUNT) return; /* Unknown key codes fail closed. */
  k->physical[code]=(state==WL_KEYBOARD_KEY_STATE_PRESSED);
  if (!allow) { refresh(k); return; }
  if (state==WL_KEYBOARD_KEY_STATE_RELEASED && !k->forwarded[code]) return;
  if (state==WL_KEYBOARD_KEY_STATE_PRESSED && k->forwarded[code]) return;
  k->forwarded[code]=(state==WL_KEYBOARD_KEY_STATE_PRESSED);
  if (k->original->key) k->original->key(k->data,p,serial,time,code,state);
}
static void modifiers(void *data,struct wl_keyboard *p,uint32_t serial,uint32_t depressed,uint32_t latched,uint32_t locked,uint32_t group) {
  struct keyboard *k=data; k->serial=serial;
  if (refresh(k) && k->original->modifiers) k->original->modifiers(k->data,p,serial,depressed,latched,locked,group);
}
static void repeat(void *data,struct wl_keyboard *p,int32_t rate,int32_t delay) {
  struct keyboard *k=data;
  if (k->original->repeat_info) k->original->repeat_info(k->data,p,rate,delay);
}
static const struct wl_keyboard_listener listener={keymap,enter,leave,key,modifiers,repeat};
int wl_proxy_add_listener(struct wl_proxy *proxy,void (**implementation)(void),void *data) {
  static int (*real_add)(struct wl_proxy *,void (**)(void),void *);
  if (!real_add) real_add=dlsym(RTLD_NEXT,"wl_proxy_add_listener");
  initialize();
  if (!lease_path || strcmp(wl_proxy_get_class(proxy),"wl_keyboard")) return real_add(proxy,implementation,data);
  struct keyboard *k=calloc(1,sizeof *k);
  if (!k) return -1;
  k->proxy=proxy; k->original=(const struct wl_keyboard_listener *)implementation; k->data=data;
  int ret=real_add(proxy,(void (**)(void))&listener,k);
  if (ret) { free(k); return ret; }
  k->next=keyboards; keyboards=k;
  fprintf(stderr,"omadev gate: host keyboard intercepted, default closed\n");
  return 0;
}
void wl_proxy_destroy(struct wl_proxy *proxy) {
  static void (*real_destroy)(struct wl_proxy *);
  if (!real_destroy) real_destroy=dlsym(RTLD_NEXT,"wl_proxy_destroy");
  struct keyboard **link=&keyboards;
  while (*link) {
    struct keyboard *k=*link;
    if (k->proxy==proxy) { release_forwarded(k); *link=k->next; free(k); break; }
    link=&k->next;
  }
  real_destroy(proxy);
}
