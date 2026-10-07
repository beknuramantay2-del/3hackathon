from .security import FULL_GUARD, OS


class HotkeyGuard:
    def __init__(
        self,
        hotkeys,
        emergency="ctrl+shift+f12",
        on_block=None,
        on_exit=None,
        enabled=True,
    ):
        self.hotkeys = hotkeys
        self.emergency = emergency
        self.on_block = on_block
        self.on_exit = on_exit
        self.enabled = enabled
        self.kb = None
        self.status = "disabled"
        self.errors = []
        self.monitor_only = not FULL_GUARD

    def block_keys(self):

        if self.monitor_only:
            print("Мониторинг (не блокирует): клавиши только логируются")
            return False
        self.start()
        return True

    def start(self):
        if not self.enabled:
            self.status = "disabled"
            return False
        if self.monitor_only:
            print(f"[hotkey] {OS}: режим мониторинга, suppress недоступен.")
            self.status = "monitor-only"
            return False
        try:
            import keyboard

            self.kb = keyboard
            self.errors = []
            skip = (self.emergency or "").strip().lower()
            for hk in self.hotkeys:
                if (
                    hk.strip().lower() == skip
                    or hk.strip().lower() == skip.split("+")[-1]
                ):
                    continue
                try:
                    keyboard.add_hotkey(hk, lambda h=hk: self._cb(h), suppress=True)
                except Exception as e:
                    self.errors.append(f"{hk}: {e}")
                    print(f"[hotkey] {hk}: {e}")
            keyboard.add_hotkey(self.emergency, self._exit)
            self.status = "partial" if self.errors else "active"
            return not self.errors
        except Exception as e:
            self.status = "error"
            self.errors.append(str(e))
            print("[hotkey] disabled:", e)
            return False

    def _cb(self, hk):
        if self.on_block:
            self.on_block(f"HOTKEY_BLOCKED:{hk}")

    def _exit(self):
        if self.on_exit is not None:
            self.on_exit()
        else:
            self.stop()

    def stop(self):
        try:
            if self.kb:
                self.kb.unhook_all()
        except Exception:
            pass
