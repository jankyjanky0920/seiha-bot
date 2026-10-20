"""スコアの端数処理とショップ商品の基本チェック。"""
import unittest
from datetime import datetime, timedelta, timezone

from alien_bot.score_store import PRODUCTS, duration_reward, score_text


class ScoreCalculations(unittest.TestCase):
    def test_rewards_round_up_to_thousandths(self):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        for minutes, expected in [
            (0, 0), (1, 17), (10, 167), (30, 500),
            (60, 1000), (90, 1500), (181, 3017),
        ]:
            with self.subTest(minutes=minutes):
                self.assertEqual(
                    duration_reward(start, start + timedelta(minutes=minutes)),
                    (minutes, expected),
                )

    def test_ignore_partial_minutes(self):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.assertEqual(
            duration_reward(start, start + timedelta(minutes=1, seconds=59)),
            (1, 17),
        )
        self.assertEqual(
            duration_reward(start, start + timedelta(seconds=59)),
            (0, 0),
        )

    def test_prices(self):
        self.assertEqual(
            {k: price for k, (name, price, desc) in PRODUCTS.items()},
            {
                "shorts": 2000, "commission": 3000,
                "puzzle": 1000, "discord_bot": 5000, "website": 5000,
            },
        )
        self.assertEqual(score_text(17), "0.017")
        self.assertEqual(score_text(1203), "1.203")


if __name__ == "__main__":
    unittest.main()
