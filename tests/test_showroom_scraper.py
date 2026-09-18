import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from bot.showroom_scraper import ShowroomScraper


class TestShowroomScraper(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scraper = ShowroomScraper(timeout_seconds=2.0)

    async def asyncTearDown(self):
        await self.scraper.close()

    @patch("httpx.AsyncClient.get")
    async def test_get_live_streaming_url_active(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "streaming_url_list": [
                {
                    "url": "https://example.com/live/original.m3u8",
                    "label": "original",
                    "is_default": True,
                    "type": "hls",
                },
                {
                    "url": "https://example.com/live/low.m3u8",
                    "label": "low",
                    "is_default": False,
                    "type": "hls",
                },
            ]
        }
        mock_get.return_value = mock_resp

        url = await self.scraper.get_live_streaming_url(318218)
        self.assertEqual(url, "https://example.com/live/original.m3u8")

    @patch("httpx.AsyncClient.get")
    async def test_get_live_streaming_url_offline(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"streaming_url_list": []}
        mock_get.return_value = mock_resp

        url = await self.scraper.get_live_streaming_url(318218)
        self.assertIsNone(url)

    @patch("httpx.AsyncClient.get")
    async def test_is_room_live(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "streaming_url_list": [
                {"url": "https://example.com/live.m3u8", "type": "hls"}
            ]
        }
        mock_get.return_value = mock_resp

        is_live = await self.scraper.is_room_live(318218)
        self.assertTrue(is_live)


if __name__ == "__main__":
    unittest.main()
