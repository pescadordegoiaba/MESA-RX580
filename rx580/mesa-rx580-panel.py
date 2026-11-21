#!/usr/bin/env python3
# MESA-RX580 settings panel. Writes ~/.drirc options read by RADV at device creation.

import os
import xml.etree.ElementTree as ET

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

DRIRC = os.path.join(os.path.expanduser("~"), ".drirc")
MARKER = "MESA-RX580"

# name, kind, default, minimum, maximum, label, help
# Written by older panel builds. The driver ignores them. Apply removes them.
STRIP = {
    "radv_rx580_dcc_min",
    "radv_rx580_dcc_fast_clear",
    "radv_rx580_dcc_max",
    "radv_rx580_pin_vram",
    "radv_rx580_pin_buffer_kb",
    "radv_rx580_large_heap_mb",
    "radv_rx580_transfer_queue",
    "radv_rx580_float16",
}

OPTIONS = [
    ("radv_rx580_htile_min", "int", 8, 8, 256,
     "HTILE em depth sem MSAA a partir de (px)",
     "O driver não aceita menos que 8. Esse é o piso do Mesa. Subir desliga HTILE em depth pequeno."),
    ("radv_rx580_inherited_conditional", "bool", True, None, None,
     "inheritedConditionalRendering",
     "Predicação herdada por command buffer secundário, em software."),
]


def defaults():
    return {item[0]: item[2] for item in OPTIONS}


def fmt(kind, value):
    if kind == "bool":
        return "true" if value else "false"
    return str(int(value))


def parse(kind, text):
    if kind == "bool":
        return text.strip().lower() in ("true", "1", "yes")
    return int(text)


def load_values():
    values = defaults()
    if not os.path.isfile(DRIRC):
        return values
    try:
        tree = ET.parse(DRIRC)
    except ET.ParseError:
        return values
    root = tree.getroot()
    for device in root.findall("device"):
        if device.get("driver") != "radv":
            continue
        for option in device.findall("option"):
            name = option.get("name")
            if name in values and option.get("value") is not None:
                kind = next(item[1] for item in OPTIONS if item[0] == name)
                try:
                    values[name] = parse(kind, option.get("value"))
                except ValueError:
                    pass
    return values


def save_values(values):
    if os.path.isfile(DRIRC):
        try:
            tree = ET.parse(DRIRC)
            root = tree.getroot()
        except ET.ParseError as exc:
            raise RuntimeError(f"{DRIRC} não é um XML válido: {exc}") from exc
    else:
        root = ET.Element("driconf")
        tree = ET.ElementTree(root)

    device = None
    for candidate in root.findall("device"):
        if candidate.get("driver") == "radv":
            device = candidate
            break
    if device is None:
        device = ET.SubElement(root, "device", driver="radv")

    wanted = {item[0] for item in OPTIONS} | STRIP
    for option in list(device.findall("option")):
        if option.get("name") in wanted:
            device.remove(option)

    comment = None
    if device.text and MARKER in device.text:
        comment = device.text
    device.text = f"\n    {comment or MARKER}\n    "

    kinds = {item[0]: item[1] for item in OPTIONS}
    for name, _kind, *_rest in OPTIONS:
        ET.SubElement(device, "option", name=name, value=fmt(kinds[name], values[name]))

    ET.indent(tree, space="  ")
    tmp = DRIRC + ".tmp"
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, DRIRC)


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MESA-RX580")
        self.set_default_size(560, 720)
        self.widgets = {}

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        title = Gtk.Label(label="MESA-RX580")
        title.add_css_class("title")
        header.set_title_widget(title)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(scroll)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(16)
        root.set_margin_end(16)
        scroll.set_child(root)

        intro = Gtk.Label(
            label="Ajustes do RADV para a RX 580 (Polaris 10). "
                  "Eles valem no próximo processo Vulkan. O painel grava ~/.drirc. "
                  "DCC extra, fila SDMA, pin de VRAM e shaderFloat16 foram retirados: "
                  "os três primeiros piscam ou oscilam o FPS, e o float16 aumenta VGPR "
                  "conforme shaders novos entram na sessão. Aplicar apaga essas chaves antigas.",
            wrap=True,
            xalign=0,
        )
        root.append(intro)

        self.status = Gtk.Label(label="", wrap=True, xalign=0)
        self.status.add_css_class("dim-label")

        for name, kind, _default, low, high, label, help_text in OPTIONS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            caption = Gtk.Label(label=label, xalign=0, hexpand=True)
            row.append(caption)
            if kind == "bool":
                widget = Gtk.Switch(valign=Gtk.Align.CENTER)
            else:
                widget = Gtk.SpinButton.new_with_range(low, high, 1)
                widget.set_numeric(True)
            self.widgets[name] = widget
            row.append(widget)
            box.append(row)
            hint = Gtk.Label(label=help_text, wrap=True, xalign=0)
            hint.add_css_class("dim-label")
            box.append(hint)
            root.append(box)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        reset = Gtk.Button(label="Restaurar padrões")
        reset.connect("clicked", self.on_reset)
        apply = Gtk.Button(label="Aplicar")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", self.on_apply)
        buttons.append(reset)
        buttons.append(apply)
        root.append(buttons)
        root.append(self.status)
        self.load()

    def collect(self):
        values = {}
        for name, kind, *_rest in OPTIONS:
            widget = self.widgets[name]
            if kind == "bool":
                values[name] = widget.get_active()
            else:
                values[name] = int(widget.get_value())
        return values

    def show(self, values):
        for name, kind, *_rest in OPTIONS:
            widget = self.widgets[name]
            if kind == "bool":
                widget.set_active(bool(values[name]))
            else:
                widget.set_value(int(values[name]))

    def load(self):
        self.show(load_values())
        self.status.set_text(f"Arquivo: {DRIRC}")

    def on_reset(self, _button):
        self.show(defaults())
        self.status.set_text("Padrões na tela. Aplicar grava o arquivo.")

    def on_apply(self, _button):
        try:
            save_values(self.collect())
        except Exception as exc:
            self.status.set_text(str(exc))
            return
        self.status.set_text(f"Gravado em {DRIRC}. Feche e abra de novo o jogo ou o app Vulkan.")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.mesa.rx580.panel")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = Panel(self)
        win.present()


if __name__ == "__main__":
    raise SystemExit(App().run(None))
