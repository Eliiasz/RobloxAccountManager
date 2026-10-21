import unittest
from unittest.mock import patch

from features import account_actions as actions


def _state(pid, left, top, w=384, h=270, show=2):
    # placement: (flags, showCmd, minPos, maxPos, normalRect)
    return {"pid": pid, "hwnd": 1000 + pid, "visible": True, "headless_hidden": False,
            "placement": (0, show, (-1, -1), (-1, -1), (left, top, left + w, top + h))}


class GridOrderTests(unittest.TestCase):
    def test_top_left_first_then_row_by_row(self):
        # A 3 x 2 grid given in random order; small offsets within a row are fine.
        states = [_state(6, 768, 272), _state(1, 0, 0), _state(5, 384, 270), _state(3, 768, 3),
                  _state(2, 384, 1), _state(4, 0, 271)]
        self.assertEqual([s["pid"] for s in actions._afk_grid_order(states)], [1, 2, 3, 4, 5, 6])

    def test_minimized_windows_keep_their_grid_place(self):
        states = [_state(2, 384, 0, show=2), _state(1, 0, 0, show=2)]  # showCmd 2 = minimized
        self.assertEqual([s["pid"] for s in actions._afk_grid_order(states)], [1, 2])


class RoundTests(unittest.TestCase):
    def setUp(self):
        actions._afk_stop_event.clear()

    def _run(self, states, delay=1.0):
        events = []
        waits = []

        def fake_wait(seconds):
            waits.append(seconds)
            return False

        with patch.object(actions, "afk_window_delay", return_value=delay), \
                patch.object(actions, "_afk_show_window", side_effect=lambda s: events.append(("show", s["pid"]))), \
                patch.object(actions, "_focus_afk_window", side_effect=lambda h: events.append(("focus", h)) or True), \
                patch.object(actions, "_perform_afk_action", side_effect=lambda *a: events.append(("key",)) or True), \
                patch.object(actions, "_restore_afk_window_state",
                             side_effect=lambda s, m: events.append(("back", s["pid"]))), \
                patch.object(actions.win32gui, "IsWindow", return_value=True), \
                patch.object(actions._afk_stop_event, "wait", side_effect=fake_wait):
            done = actions._afk_run_round(states, None, 77)
        return done, events, waits

    def test_one_window_at_a_time_and_put_back_before_the_next(self):
        done, events, waits = self._run([_state(2, 384, 0), _state(1, 0, 0)])
        self.assertEqual(done, 2)
        shows_and_backs = [e for e in events if e[0] in ("show", "back")]
        self.assertEqual(shows_and_backs, [("show", 1), ("back", 1), ("show", 2), ("back", 2)])
        self.assertEqual(events[-1], ("focus", 77))  # focus returns to what you were using
        self.assertIn(1.0, waits)  # the delay between windows

    def test_no_delay_after_the_last_window(self):
        _done, _events, waits = self._run([_state(1, 0, 0)], delay=3.0)
        self.assertNotIn(3.0, waits)

    def test_delay_setting_is_clamped(self):
        with patch.object(actions, "get_ui_setting", return_value=99):
            self.assertEqual(actions.afk_window_delay(), actions.AFK_DELAY_MAX)
        with patch.object(actions, "get_ui_setting", return_value="bad"):
            self.assertEqual(actions.afk_window_delay(), actions.AFK_DELAY_DEFAULT)

    def test_never_maximizes(self):
        import inspect
        self.assertNotIn("SW_MAXIMIZE", inspect.getsource(actions._afk_run_round))
        self.assertNotIn("SW_MAXIMIZE", inspect.getsource(actions._afk_show_window))


if __name__ == "__main__":
    unittest.main()
