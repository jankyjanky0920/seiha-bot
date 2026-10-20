"""スコアの端数処理とショップ商品の基本チェック。"""
import unittest
from datetime import datetime, timedelta, timezone
from importlib import import_module

from alien_bot.score_store import PRODUCTS, duration_reward, score_text, can_earn_shoot_score, is_worker


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

    def test_discord_cog_modules_import(self):
        # スラッシュコマンドのannotationやデコレータの問題を検知する。
        import_module("alien_bot.cogs.shooting")
        import_module("alien_bot.cogs.shop")

    def test_admin_can_earn_without_workspace(self):
        from types import SimpleNamespace
        admin = SimpleNamespace(bot=False, roles=[SimpleNamespace(id=1541554412126994594)])
        worker = SimpleNamespace(bot=False, roles=[])
        bot = SimpleNamespace(bot=True, roles=[])
        self.assertTrue(can_earn_shoot_score(admin))
        self.assertTrue(can_earn_shoot_score(worker))
        self.assertFalse(can_earn_shoot_score(bot))
        self.assertFalse(is_worker(admin))  # 管理者の作業場は作らない

    def test_command_names_changed_only(self):
        from alien_bot.cogs.shooting import ShootingCog
        from alien_bot.cogs.shop import ShopCog
        self.assertEqual(ShootingCog.shootstart.name, "zzzadmin_shootstart")
        self.assertEqual(ShootingCog.shootfinish.name, "zzzadmin_shootfinish")
        self.assertEqual(ShopCog.requests.name, "zzzadmin_shoprequests")
        self.assertEqual(ShopCog.admin_decision.name, "zzzadmin_shopdecision")
        self.assertEqual(ShopCog.score.name, "score")
        self.assertEqual(ShopCog.shop.name, "shop")

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
