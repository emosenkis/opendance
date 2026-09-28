import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from opendance.editor import apply_edit, editor_state, save_edit
from opendance.extract import _build_move_scoring
from opendance.game import GameSession, named_pose


def extracted_song():
    timeline = []
    for index in range(25):
        time_s = index * 0.25
        timeline.append(
            {
                "timestamp_ms": time_s * 1_000,
                "people": [
                    {"dancer_index": 0, "keypoints": named_pose("ready" if time_s < 3 else "star")},
                    {"dancer_index": 1, "keypoints": named_pose("clap" if time_s < 3 else "ready")},
                ],
            }
        )
    return {
        "id": "editable",
        "title": "Editable",
        "duration": 6.0,
        "video": "video.mp4",
        "choreography": {
            "dancers": [{"index": 0}, {"index": 1}],
            "timeline": timeline,
            "move_scoring": _build_move_scoring(timeline, 2),
        },
    }


class DanceEditorTest(unittest.TestCase):
    def test_role_swap_power_cue_and_masks_are_non_destructive(self):
        song = extracted_song()
        original = json.dumps(song, sort_keys=True)

        swapped = apply_edit(
            song, {"action": "swap", "start": 0.0, "end": 3.0, "first": 0, "second": 1}
        )
        powered = apply_edit(swapped, {"action": "power", "segment": 0, "enabled": True})
        cued = apply_edit(powered, {"action": "cue", "segment": 0, "delta": -1})
        scoring = cued["choreography"]["move_scoring"]
        definition_id = scoring["segments"][0]["dancers"][0]["definition"]
        joint = scoring["definitions"][definition_id]["important_joints"][0]
        arrowed = apply_edit(cued, {"action": "joint", "segment": 0, "joint": joint})
        masked = apply_edit(arrowed, {"action": "mask", "start": 2.5, "end": 3.5})

        self.assertEqual(json.dumps(song, sort_keys=True), original)
        self.assertEqual(swapped["choreography"]["timeline"][0]["people"][0]["dancer_index"], 1)
        self.assertTrue(powered["choreography"]["move_scoring"]["segments"][0]["power"])
        session = GameSession(powered)
        feedback = []
        for index in range(14):
            feedback.extend(session.update(index * 0.25, {1: named_pose("ready")}))
        self.assertTrue(feedback)
        self.assertTrue(feedback[0].power)
        self.assertNotEqual(
            cued["choreography"]["move_scoring"]["definitions"],
            powered["choreography"]["move_scoring"]["definitions"],
        )
        self.assertNotEqual(
            arrowed["choreography"]["move_scoring"]["definitions"],
            cued["choreography"]["move_scoring"]["definitions"],
        )
        self.assertEqual(
            masked["choreography"]["move_scoring"]["score_masks"],
            [{"start": 2.5, "end": 3.5}],
        )
        masked_session = GameSession(masked)
        self.assertEqual(masked_session._segments, [])
        self.assertEqual(masked_session.moves, [])

    def test_split_and_merge_rebuild_definitions_from_the_timeline(self):
        song = extracted_song()
        first = song["choreography"]["move_scoring"]["segments"][0]
        split_at = (first["start"] + first["end"]) / 2

        split = apply_edit(song, {"action": "split", "segment": 0, "time": split_at})
        self.assertEqual(split["choreography"]["move_scoring"]["segments"][0]["end"], split_at)
        self.assertEqual(split["choreography"]["move_scoring"]["segments"][1]["start"], split_at)

        merged = apply_edit(split, {"action": "merge", "segment": 0})
        segment = merged["choreography"]["move_scoring"]["segments"][0]
        self.assertEqual(segment["start"], first["start"])
        self.assertEqual(segment["end"], first["end"])
        self.assertTrue(all(item["definition"].startswith("edit") for item in segment["dancers"]))

    def test_cues_can_be_edited_for_one_dancer(self):
        song = extracted_song()
        scoring = song["choreography"]["move_scoring"]
        segment = scoring["segments"][0]
        before = {
            dancer["dancer_index"]: scoring["definitions"][dancer["definition"]]["cue_sample"]
            for dancer in segment["dancers"]
        }

        edited = apply_edit(
            song,
            {"action": "cue", "segment": 0, "delta": -1, "dancer_index": 1},
        )
        edited_scoring = edited["choreography"]["move_scoring"]
        after = {
            dancer["dancer_index"]: edited_scoring["definitions"][dancer["definition"]]["cue_sample"]
            for dancer in edited_scoring["segments"][0]["dancers"]
        }

        self.assertEqual(after[0], before[0])
        self.assertNotEqual(after[1], before[1])

    def test_save_is_atomic_backed_up_and_exposes_editor_state(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.json"
            video = path.parent / "video.mp4"
            video.write_bytes(b"video")
            song = extracted_song()
            path.write_text(json.dumps(song), encoding="utf-8")

            edited = save_edit(path, {"action": "power", "segment": 0, "enabled": True})
            state = editor_state(edited, path)

            self.assertEqual(
                json.loads(path.with_name("song.json.editor.bak").read_text()),
                json.loads(json.dumps(song)),
            )
            self.assertTrue(json.loads(path.read_text())["choreography"]["move_scoring"]["segments"][0]["power"])
            self.assertTrue(state["video"].startswith("file:"))
            self.assertEqual(state["dancer_count"], 2)


if __name__ == "__main__":
    unittest.main()
