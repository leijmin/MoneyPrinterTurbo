#!/usr/bin/env python
import os
import sys
import argparse
from loguru import logger

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.services.video_metadata import VideoMetadataExtractor
from app.services.preprocess_video import VideoPreprocessor
from app.models.schema import MaterialInfo
from app.services.video import get_video_rotation, get_video_codec


def setup_logger():
    """配置日志"""
    logger.remove()  # 移除默认处理程序
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
        level="INFO"
    )


def analyze_video(video_path, preprocess=False):
    """分析视频元数据"""
    if not os.path.exists(video_path):
        logger.error(f"视频文件不存在: {video_path}")
        return

    logger.info(f"开始分析视频: {video_path}")
    
    # 获取视频元数据
    metadata = VideoMetadataExtractor.get_video_metadata(video_path)
    logger.info("视频元数据:")
    for k, v in metadata.items():
        logger.info(f"  {k}: {v}")
    
    # 是否预处理视频
    if preprocess:
        logger.info("开始预处理视频...")
        m = MaterialInfo()
        m.url = video_path
        m.provider = "local"
        
        # 使用预处理函数处理视频
        processed_materials = VideoPreprocessor.preprocess_video([m], clip_duration=4)
        
        if processed_materials and len(processed_materials) > 0:
            processed_path = processed_materials[0].url
            logger.info(f"预处理后的视频: {processed_path}")
            
            # 如果生成了新文件，分析处理后的视频
            if processed_path != video_path and os.path.exists(processed_path):
                logger.info("分析预处理后的视频元数据:")
                processed_metadata = VideoMetadataExtractor.get_video_metadata(processed_path)
                for k, v in processed_metadata.items():
                    logger.info(f"  {k}: {v}")


def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="视频元数据分析与处理工具")
    parser.add_argument("video_path", help="视频文件路径")
    parser.add_argument("-p", "--preprocess", action="store_true", help="同时执行视频预处理")
    args = parser.parse_args()
    
    # 设置日志
    setup_logger()
    
    # 分析并处理视频
    analyze_video(args.video_path, args.preprocess)


if __name__ == "__main__":
    main() 