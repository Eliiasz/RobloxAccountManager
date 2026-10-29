import threading
import unittest
from unittest.mock import Mock, patch

from classes.operation_result import OperationResult
from features import account_actions as actions


class JoinUserTests(unittest.TestCase):
    def run_join(self, presence, usernames=("alice", "bob")):
        manager = Mock()
        manager.accounts = {name: {"cookie": "TEST"} for name in usernames}
        manager.launch_roblox.return_value = OperationResult.success()
        launched = []
        finished = threading.Event()
        results = []

        def fake_batch(names, launch, delay, label):
            for name in names:
                launch(name, False)
                launched.append(name)
            return len(names), [], []

        def done(ok, result):
            results.append(result)
            finished.set()

        with patch.object(actions.RobloxAPI, "get_user_id_from_username", return_value=4242), \
                patch.object(actions.RobloxAPI, "get_player_presence", return_value=presence), \
                patch.object(actions, "_run_launch_batch", side_effect=fake_batch), \
                patch.object(actions, "_batch_launch_result", return_value=OperationResult.success("Joined")), \
                patch.object(actions, "load_ui_settings", return_value={}):
            actions.join_user(manager, list(usernames), "target", on_done=done)
            self.assertTrue(finished.wait(5))
        return manager, launched, results[0]

    def test_every_account_joins_the_user_through_roblox(self):
        manager, launched, result = self.run_join({"in_game": True, "place_id": 1818, "game_id": "job"})
        self.assertTrue(result)
        self.assertEqual(launched, ["alice", "bob"])
        for call in manager.launch_roblox.call_args_list:
            self.assertEqual(call.kwargs["follow_user_id"], "4242")
            self.assertNotIn("job_id", call.kwargs)

    def test_a_hidden_server_still_tries_to_join(self):
        manager, launched, result = self.run_join({"in_game": True, "place_id": None, "game_id": None})
        self.assertTrue(result)
        self.assertEqual(launched, ["alice", "bob"])

    def test_an_offline_user_launches_nothing(self):
        manager, launched, result = self.run_join({"in_game": False, "status": 0})
        self.assertFalse(result)
        self.assertEqual(result.code, "TARGET_NOT_IN_GAME")
        manager.launch_roblox.assert_not_called()


if __name__ == "__main__":
    unittest.main()
