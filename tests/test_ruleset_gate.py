import unittest


class RulesetGateProbe(unittest.TestCase):
    def test_deliberately_red(self):
        self.fail("deliberate failure: proves the main ruleset blocks a red check; never merge")
