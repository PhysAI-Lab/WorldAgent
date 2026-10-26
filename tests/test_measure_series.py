"""numerical.trend / numerical.within and the verifier's cache-missing convention (no Blender).

    python3 -m unittest worldagent-plugin/tests/test_measure_series.py
"""
import math
import subprocess
import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))
from worldagent_measure import numerical, verifier  # noqa: E402


def rows(values, key="v"):
    return [{"frame": 10 + i, key: v} for i, v in enumerate(values)]


class TrendTest(unittest.TestCase):
    def test_monotone_passes(self):
        c = numerical.trend(rows([0, 1, 2, 3]), "v", direction="increasing", min_total_change=3, max_reversal=0)
        self.assertTrue(c.passed)
        self.assertEqual((3.0, 0.0, None), (c.measurements["total_change"], c.measurements["max_reversal"],
                                            c.measurements["max_reversal_frames"]))
        self.assertEqual([10, 11, 12, 13], c.measurements["frames"])

    def test_decreasing_is_signed(self):
        c = numerical.trend(rows([5, 4, 4.1, 1]), "v", direction="decreasing", min_total_change=4, max_reversal=0.2)
        self.assertTrue(c.passed)
        self.assertAlmostEqual(4.0, c.measurements["total_change"])
        self.assertAlmostEqual(0.1, c.measurements["max_reversal"])
        self.assertEqual([11, 12], c.measurements["max_reversal_frames"])

    def test_flat_fails_min_change(self):
        c = numerical.trend(rows([2, 2, 2]), "v", direction="increasing", min_total_change=0.5, max_reversal=1)
        self.assertFalse(c.passed)
        self.assertEqual(("total change", 0.0, 0.5, 12), (c.first_failure["reason"], c.first_failure["value"],
                                                          c.first_failure["limit"], c.first_failure["frame"]))

    def test_reversal_above_max_fails(self):
        c = numerical.trend(rows([0, 2, 1, 4]), "v", direction="increasing", min_total_change=1, max_reversal=0.5)
        self.assertFalse(c.passed)
        f = c.first_failure
        self.assertEqual(("reversal", 1.0, 0.5, 12, 11), (f["reason"], f["value"], f["limit"], f["frame"],
                                                          f["previous_frame"]))

    def test_missing_none_nan_fail(self):
        for bad in ([{"frame": 1, "v": 1}, {"frame": 2}], rows([1, None]), rows([1, math.nan]), rows([1, "x"])):
            c = numerical.trend(bad, "v", direction="increasing", min_total_change=0, max_reversal=0)
            self.assertFalse(c.passed)
            self.assertEqual(("missing value", 2 if bad[0]["frame"] == 1 else 11),
                             (c.first_failure["reason"], c.first_failure["frame"]))

    def test_key_path(self):
        data = [{"frame": 1, "q": {"0.99": 1.0}, "xs": [0, 5]}, {"frame": 2, "q": {"0.99": 3.0}, "xs": [0, 6]}]
        c = numerical.trend(data, ("q", "0.99"), direction="increasing", min_total_change=2, max_reversal=0)
        self.assertTrue(c.passed)
        self.assertEqual("q.0.99", c.measurements["metric"])
        self.assertTrue(numerical.trend(data, ["xs", 1], direction="increasing", min_total_change=1,
                                        max_reversal=0).passed)

    def test_argument_errors(self):
        with self.assertRaises(ValueError):
            numerical.trend(rows([1]), "v", direction="increasing", min_total_change=0, max_reversal=0)
        with self.assertRaises(ValueError):
            numerical.trend(rows([1, 2]), "v", direction="up", min_total_change=0, max_reversal=0)
        with self.assertRaises(ValueError):
            numerical.trend(rows([1, 2]), "v", direction="increasing", min_total_change=0, max_reversal=-1)
        with self.assertRaises(TypeError):  # every limit is required
            numerical.trend(rows([1, 2]), "v", direction="increasing", min_total_change=0)


class WithinTest(unittest.TestCase):
    def test_bounds_inclusive(self):
        self.assertTrue(numerical.within(rows([1, 2, 3]), "v", lo=1, hi=3).passed)
        self.assertTrue(numerical.within(rows([1, 2, 3]), "v", lo=None, hi=3).passed)
        self.assertTrue(numerical.within(rows([1, 2, 3]), "v", lo=1, hi=None).passed)

    def test_out_of_range_fails_at_first_row(self):
        c = numerical.within(rows([1, 5, 0, 4]), "v", lo=1, hi=4)
        self.assertFalse(c.passed)
        self.assertEqual(("above hi", 11, 5.0, 4), (c.first_failure["reason"], c.first_failure["frame"],
                                                    c.first_failure["value"], c.first_failure["limit"]))
        self.assertEqual((2, [11, 12], 0.0, 5.0), (c.measurements["out_of_range_count"],
                                                   c.measurements["out_of_range_frames"],
                                                   c.measurements["min"], c.measurements["max"]))
        below = numerical.within(rows([1, 0]), "v", lo=1, hi=None)
        self.assertEqual(("below lo", 1), (below.first_failure["reason"], below.first_failure["limit"]))

    def test_missing_value_fails(self):
        c = numerical.within(rows([1, math.inf, 2]), "v", lo=0, hi=None)
        self.assertFalse(c.passed)
        self.assertEqual(("missing value", 11), (c.first_failure["reason"], c.first_failure["frame"]))

    def test_argument_errors(self):
        with self.assertRaises(ValueError):
            numerical.within(rows([1]), "v", lo=None, hi=None)
        with self.assertRaises(ValueError):
            numerical.within(rows([1]), "v", lo=2, hi=1)
        with self.assertRaises(ValueError):
            numerical.within([], "v", lo=0, hi=1)
        with self.assertRaises(TypeError):
            numerical.within(rows([1]), "v", lo=0)

    def test_result_is_json_safe(self):
        c = numerical.within(rows([1, math.nan]), "v", lo=0, hi=2)
        self.assertEqual("fail", verifier.result([c])["status"])


class ImportTest(unittest.TestCase):
    def test_numerical_imports_without_bpy(self):
        proc = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                               "import worldagent_measure.numerical; assert 'bpy' not in sys.modules", str(LIB)],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)


class CacheMissingTest(unittest.TestCase):
    def test_cache_missing_check_records_fail(self):
        try:
            raise verifier.CacheMissing("Emitter/Particles: frames 1-5 not cached")
        except verifier.CacheMissing as exc:
            c = verifier.cache_missing_check("particle_cache", exc, system="Emitter/Particles")
        self.assertFalse(c.passed)
        self.assertEqual({"cache_missing": True, "error": "Emitter/Particles: frames 1-5 not cached",
                          "system": "Emitter/Particles"}, c.measurements)
        out = verifier.result([c], frames=[1, 5])
        self.assertEqual(("fail", "particle_cache", "cache missing"),
                         (out["status"], out["first_failure"]["check"], out["first_failure"]["reason"]))

    def test_is_a_runtime_error(self):
        self.assertTrue(issubclass(verifier.CacheMissing, RuntimeError))
        with self.assertRaises(TypeError):
            verifier.cache_missing_check("x", RuntimeError("not a cache"))

    def test_solver_subclasses(self):
        """gas and liquid need bpy to import; check their base class from the source."""
        for module, cls in (("gas", "GasCacheMissing"), ("liquid", "LiquidCacheMissing")):
            text = (LIB / "worldagent_measure" / f"{module}.py").read_text(encoding="utf-8")
            self.assertIn(f"class {cls}(CacheMissing):", text)


if __name__ == "__main__":
    unittest.main()
