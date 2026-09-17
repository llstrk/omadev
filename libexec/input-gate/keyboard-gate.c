/* Host-Wayland keyboard gate, preloaded into nested Hyprland only.
 * A lease file contains: generation monotonic_deadline_ns focus_epoch pid.
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
  bool entered, open, synchronized;
  struct wl_callback *barrier;
  uint64_t generation, revoked;
  uint32_t serial, time;
  struct keyboard *next;
};
static struct keyboard *keyboards;
static char *lease_path, *status_path;
static uint64_t last_status, focus_epoch;
static struct wl_event_source *timer;
static bool initialized;

static void initialize(void) {
  if (initialized) return;
  initialized = true;
  char executable[4096];
  ssize_t length=readlink("/proc/self/exe",executable,sizeof executable-1);
  if (length<=0) return;
  executable[length]=0;
  const char *name=strrchr(executable,'/');
  if (!name || strcmp(name+1,"Hyprland")) return;
  const char *path = getenv("OMADEV_KEYBOARD_GATE");
  if (!path || !*path) return;
  lease_path = strdup(path);
  if (!lease_path || asprintf(&status_path, "%s.status", path) < 0) _exit(125);
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
  unsigned long long generation=0, deadline=0, epoch=0, pid=0;
  char trailing;
  if (sscanf(buffer, "%llu %llu %llu %llu %c", &generation, &deadline, &epoch, &pid, &trailing) != 4) return 0;
  if (epoch != focus_epoch || pid != (unsigned long long)getpid()) return 0;
  uint64_t now = now_ns();
  /* Controllers must renew frequently; a malformed far-future lease fails closed. */
  return generation && deadline > now && deadline - now <= 2000000000ULL ? generation : 0;
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
static void sync_done(void *data, struct wl_callback *callback, uint32_t serial) {
  struct keyboard *k=data;
  wl_callback_destroy(callback);
  k->barrier=NULL;
  k->synchronized=authorization()==k->generation && k->generation!=0;
}
static const struct wl_callback_listener sync_listener={sync_done};
static void cancel_barrier(struct keyboard *k) {
  if (k->barrier) wl_callback_destroy(k->barrier);
  k->barrier=NULL;
  k->synchronized=false;
}
static void synchronize(struct keyboard *k) {
  if (k->barrier || k->synchronized) return;
  /* Drain input already queued by the host before opening a new generation.
   * Keep the callback on the backend's own queue, not a guessed default queue. */
  struct wl_display *display=wl_proxy_get_display(k->proxy);
  struct wl_display *wrapper=wl_proxy_create_wrapper(display);
  if (!wrapper) _exit(125);
  wl_proxy_set_queue((struct wl_proxy *)wrapper,wl_proxy_get_queue(k->proxy));
  k->barrier=wl_display_sync(wrapper);
  wl_proxy_wrapper_destroy(wrapper);
  if (!k->barrier || wl_callback_add_listener(k->barrier,&sync_listener,k)<0) _exit(125);
  wl_display_flush(display);
}
static bool refresh(struct keyboard *k) {
  uint64_t generation = authorization();
  if (generation != k->generation) {
    if (!generation && k->generation) k->revoked = k->generation;
    release_forwarded(k);
    cancel_barrier(k);
    k->generation = generation;
  }
  if (!generation || generation == k->revoked || !k->entered) {
    release_forwarded(k);
    return false;
  }
  /* Once open, deliberate holds stay held. Only entry waits for all releases. */
  synchronize(k);
  if (!k->open && k->synchronized && !held(k)) k->open = true;
  return k->open;
}
static void publish_status(void) {
  if (!status_path || now_ns() - last_status < 50000000ULL) return;
  unsigned count=0, forwarded=0;
  uint64_t generation=0;
  bool opened=false, pending=false, revoked=false, entered=false;
  for (struct keyboard *k=keyboards;k;k=k->next) {
    ++count;
    generation=k->generation;
    opened |= k->open;
    entered |= k->entered;
    revoked |= generation && k->revoked == generation;
    pending |= generation && k->revoked != generation && !k->open;
    for (unsigned i=0;i<KEY_COUNT;++i) forwarded += k->forwarded[i];
  }
  char *tmp;
  if (asprintf(&tmp, "%s.tmp", status_path) < 0) _exit(125);
  int fd=open(tmp,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
  if (fd<0) { free(tmp); return; }
  int rc=dprintf(fd,"{\"protocol\":1,\"pid\":%d,\"updatedNs\":%llu,\"generation\":%llu,"
                 "\"focusEpoch\":%llu,\"entered\":%s,\"keyboards\":%u,\"forwarded\":%u,\"state\":\"%s\"}\n",
                 getpid(),(unsigned long long)now_ns(),(unsigned long long)generation,
                 (unsigned long long)focus_epoch,entered ? "true" : "false",
                 count,forwarded,revoked ? "revoked" : opened ? "captured" : pending ? "pending" : "uncaptured");
  close(fd);
  if (rc>0 && rename(tmp,status_path)==0) last_status=now_ns();
  free(tmp);
}
static int tick(void *data) {
  for (struct keyboard *k=keyboards;k;k=k->next) refresh(k);
  publish_status();
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
  ++focus_epoch;
  k->entered=true; k->serial=serial;
  refresh(k);
  struct wl_array empty; wl_array_init(&empty);
  if (k->original->enter) k->original->enter(k->data,p,serial,surface,&empty);
}
static void leave(void *data,struct wl_keyboard *p,uint32_t serial,struct wl_surface *surface) {
  struct keyboard *k=data;
  k->serial=serial;
  k->revoked=authorization();
  ++focus_epoch;
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
static void forget_keyboard(struct wl_proxy *proxy) {
  struct keyboard **link=&keyboards;
  while (*link) {
    struct keyboard *k=*link;
    /* Original userdata may already be destructing. Never call it here. */
    if (k->proxy==proxy) { *link=k->next; cancel_barrier(k); free(k); break; }
    link=&k->next;
  }
}
void wl_display_disconnect(struct wl_display *display) {
  static void (*real_disconnect)(struct wl_display *);
  if (!real_disconnect) real_disconnect=dlsym(RTLD_NEXT,"wl_display_disconnect");
  struct keyboard *k=keyboards;
  while (k) {
    struct keyboard *next=k->next;
    if (wl_proxy_get_display(k->proxy)==display) {
      ++focus_epoch;
      forget_keyboard(k->proxy);
    }
    k=next;
  }
  real_disconnect(display);
}
void wl_proxy_destroy(struct wl_proxy *proxy) {
  static void (*real_destroy)(struct wl_proxy *);
  if (!real_destroy) real_destroy=dlsym(RTLD_NEXT,"wl_proxy_destroy");
  forget_keyboard(proxy);
  real_destroy(proxy);
}
/* Generated clients can destroy with marshal_flags rather than proxy_destroy.
 * libwayland's variadic wrapper delegates to this public array API. */
struct wl_proxy *wl_proxy_marshal_array_flags(struct wl_proxy *proxy, uint32_t opcode,
    const struct wl_interface *interface, uint32_t version, uint32_t flags, union wl_argument *args) {
  static struct wl_proxy *(*real_marshal)(struct wl_proxy *,uint32_t,const struct wl_interface *,uint32_t,uint32_t,union wl_argument *);
  if (!real_marshal) real_marshal=dlsym(RTLD_NEXT,"wl_proxy_marshal_array_flags");
  if (flags & WL_MARSHAL_FLAG_DESTROY) forget_keyboard(proxy);
  return real_marshal(proxy,opcode,interface,version,flags,args);
}
