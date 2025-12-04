import os
import asyncio
import json
from typing import Any, Dict, List, Optional

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.client import SeedreamClient
from seedream_mcp.tools.text_to_image import handle_text_to_image
from seedream_mcp.tools.image_to_image import handle_image_to_image
from seedream_mcp.tools.multi_image_fusion import handle_multi_image_fusion
from seedream_mcp.tools.sequential_generation import handle_sequential_generation


class MockSeedreamClient(SeedreamClient):
    async def _call_api(self, endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        fmt = request_data.get("response_format", "url")
        stream = bool(request_data.get("stream"))
        items: List[Dict[str, Any]] = []
        if fmt == "url":
            items = [
                {"url": "https://example.com/a.png", "size": "2048×2048", "image_index": 0},
                {"url": "https://example.com/b.png", "size": "2048×2048", "image_index": 1},
            ]
        else:
            items = [
                {"b64_json": "iVBORw0KGgoAAAANSUhEUg==", "size": "2048×2048", "image_index": 0},
                {"b64_json": "iVBORw0KGgoAAAANSUhEUg==", "size": "2048×2048", "image_index": 1},
            ]
        usage = {"generated_images": len(items), "output_tokens": 16, "total_tokens": 16}
        status = "completed" if stream else "succeeded"
        await asyncio.sleep(0)  # yield control
        return {"success": True, "data": items, "usage": usage, "status": status}


async def _count_files(base_dir: str) -> int:
    try:
        if not os.path.isdir(base_dir):
            return 0
        count = 0
        for root, _, files in os.walk(base_dir):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")):
                    count += 1
        return count
    except Exception:
        return 0


async def run_tool_with_auto_save(tool_fn, args: Dict[str, Any], use_mock: bool) -> Dict[str, Any]:
    # ensure auto-save base dir
    base_dir = os.getenv("SEEDREAM_AUTO_SAVE_BASE_DIR", "./seedream_images")
    os.makedirs(base_dir, exist_ok=True)
    before = await _count_files(base_dir)

    if use_mock:
        # monkeypatch SeedreamClient used inside tool module
        import seedream_mcp.tools.text_to_image as tti
        import seedream_mcp.tools.image_to_image as iti
        import seedream_mcp.tools.multi_image_fusion as mif
        import seedream_mcp.tools.sequential_generation as sg
        tti.SeedreamClient = MockSeedreamClient
        iti.SeedreamClient = MockSeedreamClient
        mif.SeedreamClient = MockSeedreamClient
        sg.SeedreamClient = MockSeedreamClient

    contents = await tool_fn(args)
    after = await _count_files(base_dir)

    return {
        "text": contents[0].text if contents else "",
        "saved_delta": max(after - before, 0),
        "base_dir": base_dir,
    }


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Seedream MCP 全方位验证脚本")
    parser.add_argument("--live", action="store_true", help="启用真实API调用模式")
    parser.add_argument("--api-key", help="ARK API Key（可选，未传则读取环境变量）")
    args = parser.parse_args()

    if args.api_key:
        os.environ["ARK_API_KEY"] = args.api_key

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    api_key = os.getenv("ARK_API_KEY", "").strip()
    use_mock = (not args.live) or api_key == "" or api_key == "your_ark_api_key_here"

    print(f"Mode: {'DRY-RUN (mock)' if use_mock else 'LIVE'}")
    if not use_mock:
        cfg = SeedreamConfig.from_env()
        print(f"Config model_id={cfg.model_id}, default_size={cfg.default_size}, auto_save_dir={cfg.auto_save_base_dir}")

    scenarios = [
        (handle_text_to_image, {"prompt": "一只小猫", "size": "2K", "response_format": "url", "stream": False, "auto_save": True}, "t2i non-stream url"),
        (handle_text_to_image, {"prompt": "一只小猫", "size": "2K", "response_format": "url", "stream": True,  "auto_save": True}, "t2i stream url"),
        (handle_text_to_image, {"prompt": "一只小猫", "size": "2K", "response_format": "b64_json", "stream": False, "auto_save": True}, "t2i non-stream b64"),
        (handle_text_to_image, {"prompt": "一只小猫", "size": "2K", "response_format": "b64_json", "stream": True,  "auto_save": True}, "t2i stream b64"),

        (handle_image_to_image, {"prompt": "风格化处理", "image": "https://example.com/input.jpg", "size": "2K", "response_format": "url", "stream": True, "auto_save": True}, "i2i stream url"),
        (handle_multi_image_fusion, {"prompt": "艺术融合", "images": ["https://example.com/a.jpg", "https://example.com/b.jpg"], "size": "2K", "response_format": "url", "stream": True, "auto_save": True}, "fusion stream url"),
        (handle_sequential_generation, {"prompt": "连续生成风格图", "max_images": 3, "size": "2K", "response_format": "url", "stream": True, "auto_save": True}, "sequential stream url"),
    ]

    results = []
    for fn, args, name in scenarios:
        print(f"\n=== Running: {name} ===")
        res = await run_tool_with_auto_save(fn, args, use_mock)
        print(res["text"])  # formatted text
        print(f"[AutoSave] saved_delta={res['saved_delta']} base_dir={res['base_dir']}")
        results.append({"name": name, **res})

    # summary
    print("\n=== Summary ===")
    for r in results:
        print(json.dumps({"name": r["name"], "saved_delta": r["saved_delta"], "base_dir": r["base_dir"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
