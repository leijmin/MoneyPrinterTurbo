import os
import sys
from loguru import logger

# 添加项目根目录到路径，以便导入正常工作
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

from app.services.video_metadata import VideoMetadataExtractor
from app.services.preprocess_video import VideoPreprocessor
from app.models.schema import MaterialInfo
from app.services.video import get_video_rotation, get_video_codec


def test_video_metadata_extraction(video_path):
    """测试视频元数据提取功能"""
    logger.info(f"=== 测试视频元数据提取 ===")
    
    # 测试旋转检测
    rotation = get_video_rotation(video_path)
    logger.info(f"视频旋转角度: {rotation}°")
    
    # 测试编码检测
    codec = get_video_codec(video_path)
    logger.info(f"视频编码: {codec}")
    
    # 测试完整元数据获取
    metadata = VideoMetadataExtractor.get_video_metadata(video_path)
    logger.info(f"视频完整元数据: {metadata}")
    
    # 测试是否为竖屏视频
    is_portrait = VideoMetadataExtractor.is_portrait_by_metadata(
        metadata["width"], metadata["height"], metadata["rotation"]
    )
    logger.info(f"是否为竖屏视频: {is_portrait}")
    
    return metadata


def test_video_preprocessing(video_path):
    """测试视频预处理功能"""
    logger.info(f"=== 测试视频预处理 ===")
    
    m = MaterialInfo()
    m.url = video_path
    m.provider = "local"
    
    # 使用预处理函数处理视频
    processed_materials = VideoPreprocessor.preprocess_video([m], clip_duration=4)
    logger.info(f"预处理结果: {processed_materials}")
    
    return processed_materials


def main():
    """主测试函数"""
    # 设置日志级别
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    # 测试视频路径
    video_path = r"G:\MoneyPrinterTurbo\storage\local_videos\整机展示.mp4"
    
    if not os.path.exists(video_path):
        logger.error(f"测试视频不存在: {video_path}")
        return
    
    logger.info(f"开始测试视频处理流程...")
    logger.info(f"测试视频: {video_path}")
    
    # 1. 首先测试元数据提取
    metadata = test_video_metadata_extraction(video_path)
    
    # 2. 然后测试预处理
    processed_materials = test_video_preprocessing(video_path)
    
    # 显示预处理前后的视频路径
    if processed_materials and len(processed_materials) > 0:
        logger.info(f"预处理前视频路径: {video_path}")
        logger.info(f"预处理后视频路径: {processed_materials[0].url}")
        
        # 如果预处理生成了新文件，再次获取元数据
        if processed_materials[0].url != video_path:
            logger.info(f"=== 测试预处理后的视频元数据 ===")
            processed_metadata = test_video_metadata_extraction(processed_materials[0].url)
            
            # 对比预处理前后的元数据变化
            logger.info(f"预处理前宽高: {metadata['width']}x{metadata['height']}, 旋转: {metadata['rotation']}°")
            logger.info(f"预处理后宽高: {processed_metadata['width']}x{processed_metadata['height']}, 旋转: {processed_metadata['rotation']}°")
    
    logger.info(f"视频处理流程测试完成!")


if __name__ == "__main__":
    main() 