"""
Seedream 4.0 MCP工具集成测试

本测试模块验证MCP协议兼容性和工具集成功能。
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from seedream_mcp.server import SeedreamMCPServer
from seedream_mcp.config import SeedreamConfig


class TestMCPIntegration:
    """MCP协议集成测试类"""
    
    def __init__(self):
        self.server = None
        self.config = None
        
    async def setup(self):
        """设置测试环境"""
        print("🔧 设置测试环境...")
        
        # 设置测试配置
        os.environ.update({
            'ARK_API_KEY': 'test_api_key_12345',
            'ARK_BASE_URL': 'https://ark.cn-beijing.volces.com/api/v3',
            'SEEDREAM_MODEL_ID': 'seedream-4.0',
            'SEEDREAM_DEFAULT_SIZE': '1K',
            'SEEDREAM_DEFAULT_WATERMARK': 'true',
            'SEEDREAM_API_TIMEOUT': '60',
            'SEEDREAM_MAX_RETRIES': '3',
            'SEEDREAM_LOG_LEVEL': 'INFO'
        })
        
        # 初始化配置
        self.config = SeedreamConfig.from_env()
        print(f"✅ 配置加载完成: {self.config.model_id}")
        
        # 初始化服务器
        self.server = SeedreamMCPServer()
        print("✅ MCP服务器初始化完成")
        
    async def test_server_initialization(self):
        """测试服务器初始化"""
        print("\n📋 测试1: 服务器初始化")
        
        try:
            assert self.server is not None, "服务器未初始化"
            assert hasattr(self.server, 'tools'), "服务器缺少工具属性"
            assert len(self.server.tools) == 4, f"工具数量不正确，期望4个，实际{len(self.server.tools)}"
            
            print("✅ 服务器初始化测试通过")
            return True
        except Exception as e:
            print(f"❌ 服务器初始化测试失败: {e}")
            return False
    
    async def test_tool_registration(self):
        """测试工具注册"""
        print("\n📋 测试2: 工具注册")
        
        expected_tools = [
            'seedream_text_to_image',
            'seedream_image_to_image', 
            'seedream_multi_image_fusion',
            'seedream_sequential_generation'
        ]
        
        try:
            registered_tools = [tool.name for tool in self.server.tools]
            
            for tool_name in expected_tools:
                assert tool_name in registered_tools, f"工具 {tool_name} 未注册"
            
            print(f"✅ 工具注册测试通过，已注册工具: {registered_tools}")
            return True
        except Exception as e:
            print(f"❌ 工具注册测试失败: {e}")
            return False
    
    async def test_tool_schemas(self):
        """测试工具Schema"""
        print("\n📋 测试3: 工具Schema验证")
        
        try:
            for tool in self.server.tools:
                # 验证工具基本属性
                assert hasattr(tool, 'name'), f"工具 {tool} 缺少name属性"
                assert hasattr(tool, 'description'), f"工具 {tool.name} 缺少description属性"
                assert hasattr(tool, 'inputSchema'), f"工具 {tool.name} 缺少inputSchema属性"
                
                # 验证Schema结构
                schema = tool.inputSchema
                assert 'type' in schema, f"工具 {tool.name} Schema缺少type字段"
                assert 'properties' in schema, f"工具 {tool.name} Schema缺少properties字段"
                assert 'required' in schema, f"工具 {tool.name} Schema缺少required字段"
                
                print(f"  ✅ {tool.name}: Schema验证通过")
            
            print("✅ 工具Schema测试通过")
            return True
        except Exception as e:
            print(f"❌ 工具Schema测试失败: {e}")
            return False
    
    async def test_text_to_image_tool(self):
        """测试文生图工具"""
        print("\n📋 测试4: 文生图工具")
        
        test_args = {
            'prompt': '一只可爱的小猫咪，卡通风格',
            'size': '1K',
            'watermark': True,
            'response_format': 'url'
        }
        
        try:
            # 模拟工具调用（不实际调用API）
            tool = next(t for t in self.server.tools if t.name == 'seedream_text_to_image')
            
            # 验证参数
            schema = tool.inputSchema
            properties = schema['properties']
            
            # 验证必需参数
            assert 'prompt' in schema['required'], "prompt应该是必需参数"
            assert 'prompt' in properties, "Schema中缺少prompt属性"
            
            # 验证可选参数
            assert 'size' in properties, "Schema中缺少size属性"
            assert 'watermark' in properties, "Schema中缺少watermark属性"
            assert 'response_format' in properties, "Schema中缺少response_format属性"
            
            print("✅ 文生图工具测试通过")
            return True
        except Exception as e:
            print(f"❌ 文生图工具测试失败: {e}")
            return False
    
    async def test_image_to_image_tool(self):
        """测试图生图工具"""
        print("\n📋 测试5: 图生图工具")
        
        test_args = {
            'prompt': '将这张图片转换为油画风格',
            'image': 'https://example.com/test.jpg',
            'size': '2K',
            'watermark': False,
            'response_format': 'url'
        }
        
        try:
            tool = next(t for t in self.server.tools if t.name == 'seedream_image_to_image')
            
            # 验证参数
            schema = tool.inputSchema
            properties = schema['properties']
            required = schema['required']
            
            # 验证必需参数
            assert 'prompt' in required, "prompt应该是必需参数"
            assert 'image' in required, "image应该是必需参数"
            
            # 验证参数属性
            assert 'prompt' in properties, "Schema中缺少prompt属性"
            assert 'image' in properties, "Schema中缺少image属性"
            
            print("✅ 图生图工具测试通过")
            return True
        except Exception as e:
            print(f"❌ 图生图工具测试失败: {e}")
            return False
    
    async def test_multi_image_fusion_tool(self):
        """测试多图融合工具"""
        print("\n📋 测试6: 多图融合工具")
        
        test_args = {
            'prompt': '将这些图片融合成一个艺术作品',
            'images': [
                'https://example.com/image1.jpg',
                'https://example.com/image2.jpg',
                'https://example.com/image3.jpg'
            ],
            'size': '4K',
            'watermark': True,
            'response_format': 'url'
        }
        
        try:
            tool = next(t for t in self.server.tools if t.name == 'seedream_multi_image_fusion')
            
            # 验证参数
            schema = tool.inputSchema
            properties = schema['properties']
            required = schema['required']
            
            # 验证必需参数
            assert 'prompt' in required, "prompt应该是必需参数"
            assert 'images' in required, "images应该是必需参数"
            
            # 验证images数组属性
            images_prop = properties['images']
            assert images_prop['type'] == 'array', "images应该是数组类型"
            assert 'minItems' in images_prop, "images应该有最小项数限制"
            assert 'maxItems' in images_prop, "images应该有最大项数限制"
            
            print("✅ 多图融合工具测试通过")
            return True
        except Exception as e:
            print(f"❌ 多图融合工具测试失败: {e}")
            return False
    
    async def test_sequential_generation_tool(self):
        """测试组图生成工具"""
        print("\n📋 测试7: 组图生成工具")
        
        test_args = {
            'prompt': '科幻城市景观，未来主义风格',
            'max_images': 4,
            'size': '2K',
            'watermark': True,
            'response_format': 'url'
        }
        
        try:
            tool = next(t for t in self.server.tools if t.name == 'seedream_sequential_generation')
            
            # 验证参数
            schema = tool.inputSchema
            properties = schema['properties']
            required = schema['required']
            
            # 验证必需参数
            assert 'prompt' in required, "prompt应该是必需参数"
            
            # 验证max_images属性
            max_images_prop = properties['max_images']
            assert max_images_prop['type'] == 'integer', "max_images应该是整数类型"
            assert 'minimum' in max_images_prop, "max_images应该有最小值限制"
            assert 'maximum' in max_images_prop, "max_images应该有最大值限制"
            
            print("✅ 组图生成工具测试通过")
            return True
        except Exception as e:
            print(f"❌ 组图生成工具测试失败: {e}")
            return False
    

    
    async def test_config_validation(self):
        """测试配置验证"""
        print("\n📋 测试9: 配置验证")
        
        try:
            # 验证配置属性
            assert self.config.api_key == 'test_api_key_12345', "API密钥不匹配"
            assert self.config.base_url == 'https://ark.cn-beijing.volces.com/api/v3', "基础URL不匹配"
            assert self.config.model_id == 'seedream-4.0', "模型ID不匹配"
            assert self.config.default_size == '1K', "默认尺寸不匹配"
            assert self.config.default_watermark == False, "默认水印设置不匹配"
            assert self.config.api_timeout == 60, "API超时设置不匹配"
            assert self.config.max_retries == 3, "最大重试次数不匹配"
            assert self.config.log_level == 'INFO', "日志级别不匹配"
            
            print("✅ 配置验证测试通过")
            return True
        except Exception as e:
            print(f"❌ 配置验证测试失败: {e}")
            return False
    
    async def run_all_tests(self):
        """运行所有测试"""
        print("🚀 开始运行Seedream 4.0 MCP工具集成测试")
        print("=" * 60)
        
        # 设置测试环境
        await self.setup()
        
        # 运行测试
        tests = [
            self.test_server_initialization,
            self.test_tool_registration,
            self.test_tool_schemas,
            self.test_text_to_image_tool,
            self.test_image_to_image_tool,
            self.test_multi_image_fusion_tool,
            self.test_sequential_generation_tool,
            self.test_config_validation
        ]
        
        passed = 0
        failed = 0
        
        for test in tests:
            try:
                result = await test()
                if result:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ 测试执行异常: {e}")
                failed += 1
        
        # 输出测试结果
        print("\n" + "=" * 60)
        print("📊 测试结果汇总")
        print(f"✅ 通过: {passed}")
        print(f"❌ 失败: {failed}")
        print(f"📈 成功率: {passed/(passed+failed)*100:.1f}%")
        
        if failed == 0:
            print("\n🎉 所有测试通过！Seedream 4.0 MCP工具已准备就绪。")
        else:
            print(f"\n⚠️  有 {failed} 个测试失败，请检查相关功能。")
        
        return failed == 0


async def main():
    """主函数"""
    tester = TestMCPIntegration()
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())