import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {NightfallTray} from './tray.js';

const effectName = 'nightfall-white-quick-status';
const targetIcon = /^(network-wireless|nm-signal|nm-device-wireless|audio-volume|audio-headphones|audio-headset|battery)(-|$)/;

function recolour(icon) {
    const names = icon.gicon instanceof Gio.ThemedIcon ? icon.gicon.get_names() : [];
    const match = [icon.icon_name, ...names].some(name => name && targetIcon.test(name));
    const effect = icon.get_effect(effectName);
    if (match && !effect) {
        const white = new Clutter.BrightnessContrastEffect();
        white.set_brightness(1);
        icon.add_effect_with_name(effectName, white);
    } else if (!match && effect) {
        icon.remove_effect(effect);
    }
}

export default class NightfallPopups extends Extension {
    enable() {
        this.tray = new NightfallTray();
        this.tray.enable();
        this.actors = new Map();
        this.menu = Main.panel.statusArea.quickSettings.menu;
        this.toggle = this.menu.connect('open-state-changed', (_menu, open) => {
            if (open) this.watch(this.menu.actor);
            else this.clear();
        });
        if (this.menu.isOpen) this.watch(this.menu.actor);
    }

    watch(actor) {
        if (this.actors.has(actor)) return;
        const ids = [];
        this.actors.set(actor, ids);
        ids.push(actor.connect('child-added', (_parent, child) => this.watch(child)));
        ids.push(actor.connect('child-removed', (_parent, child) => this.forgetTree(child)));
        ids.push(actor.connect('destroy', () => this.actors.delete(actor)));
        if (actor instanceof St.Icon) {
            ids.push(actor.connect('notify::gicon', () => recolour(actor)));
            ids.push(actor.connect('notify::icon-name', () => recolour(actor)));
            recolour(actor);
        }
        for (const child of actor.get_children()) this.watch(child);
    }

    forgetTree(actor) {
        if (!this.actors.has(actor)) return;
        for (const child of actor.get_children()) this.forgetTree(child);
        this.forget(actor);
    }

    forget(actor) {
        for (const id of this.actors.get(actor)) actor.disconnect(id);
        this.actors.delete(actor);
        const effect = actor.get_effect(effectName);
        if (effect) actor.remove_effect(effect);
    }

    clear() {
        for (const actor of this.actors.keys()) this.forget(actor);
    }

    disable() {
        this.tray.disable();
        this.tray = null;
        this.menu.disconnect(this.toggle);
        this.clear();
        this.menu = null;
    }
}
