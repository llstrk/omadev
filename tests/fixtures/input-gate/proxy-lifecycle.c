/* Run as an executable named Hyprland inside an owned test nest. Exercises
 * both libwayland destruction routes while the filter watchdog is running. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include <wayland-server-core.h>
static struct wl_seat *seat;
static void global(void *data, struct wl_registry *registry, uint32_t id, const char *interface, uint32_t version) {
  if (!strcmp(interface, "wl_seat") && !seat)
    seat=wl_registry_bind(registry,id,&wl_seat_interface,version<7?version:7);
}
static void removed(void *data,struct wl_registry *registry,uint32_t id) {}
static void keymap(void *data,struct wl_keyboard *k,uint32_t format,int fd,uint32_t size) { close(fd); }
static void enter(void *d,struct wl_keyboard *k,uint32_t serial,struct wl_surface *s,struct wl_array *keys) {}
static void leave(void *d,struct wl_keyboard *k,uint32_t serial,struct wl_surface *s) {}
static void key(void *d,struct wl_keyboard *k,uint32_t serial,uint32_t time,uint32_t code,uint32_t state) {}
static void mods(void *d,struct wl_keyboard *k,uint32_t s,uint32_t dep,uint32_t lat,uint32_t lock,uint32_t group) {}
static void repeat(void *d,struct wl_keyboard *k,int32_t rate,int32_t delay) {}
static const struct wl_registry_listener registry_listener={global,removed};
static const struct wl_keyboard_listener keyboard_listener={keymap,enter,leave,key,mods,repeat};
int main(void) {
  if (!getenv("OMADEV_SLOT") || !getenv("OMADEV_OWNER") || strcmp(getenv("OMADEV_OWNER"),"input-gate-integration")) return 2;
  struct wl_display *server=wl_display_create();
  struct wl_display *client=wl_display_connect(NULL);
  if (!server || !client) return 3;
  struct wl_registry *registry=wl_display_get_registry(client);
  wl_registry_add_listener(registry,&registry_listener,NULL);
  wl_display_roundtrip(client);
  if (!seat) return 4;
  for (int i=0;i<20;++i) {
    struct wl_keyboard *keyboard=wl_seat_get_keyboard(seat);
    wl_keyboard_add_listener(keyboard,&keyboard_listener,NULL);
    wl_display_roundtrip(client);
    wl_event_loop_dispatch(wl_display_get_event_loop(server),70);
    if (i%2) wl_keyboard_release(keyboard); else wl_keyboard_destroy(keyboard);
    wl_display_roundtrip(client);
    wl_event_loop_dispatch(wl_display_get_event_loop(server),70);
  }
  wl_seat_destroy(seat);
  wl_registry_destroy(registry);
  wl_display_disconnect(client);
  wl_event_loop_dispatch(wl_display_get_event_loop(server),70);
  wl_display_destroy(server);
  puts("PASS: 20 keyboard proxy create/destroy cycles, both destruction APIs");
}
