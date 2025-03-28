#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试视频处理功能
"""
import os
import sys
from loguru import logger

# 添加项目根目录到Python路径
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.services.video_metadata import VideoMetadataExtractor
from app.services.preprocess_video import VideoPreprocessor
from app.models.schema import MaterialInfo

def test_video_metadata(video_path):
    """
    测试视频元数据提取
    """
    logger.info(f"测试视频元数据提取 - 文件: {video_path}")
    
    if not os.path.exists(video_path):
        logger.error(f"视频文件不存在: {video_path}")
        return False
    
    # 获取视频元数据
    metadata = VideoMetadataExtractor.get_video_metadata(video_path)
    
    if metadata:
        logger.info("成功获取视频元数据:")
        for key, value in metadata.items():
            logger.info(f"  {key}: {value}")
        return True
    else:
        logger.error("获取视频元数据失败")
        return False

def test_video_processing(video_path):
    """
    测试视频预处理
    """
    logger.info(f"测试视频预处理 - 文件: {video_path}")
    
    if not os.path.exists(video_path):
        logger.error(f"视频文件不存在: {video_path}")
        return False
    
    # 创建素材对象
    material = MaterialInfo()
    material.url = video_path
    material.provider = "local"
    
    # 进行视频预处理
    processed_materials = VideoPreprocessor.preprocess_video([material], clip_duration=4)
    
    if processed_materials and len(processed_materials) > 0:
        logger.info(f"视频预处理成功: {processed_materials[0].url}")
        return True
    else:
        logger.error("视频预处理失败")
        return False

def main():
    """
    主函数
    """
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    
    # 检查命令行参数
    if len(sys.argv) < 2:
        print("用法: python test_video_processing.py <视频文件路径>")
        return 1
    
    video_path = sys.argv[1]
    
    # 测试视频元数据提取
    metadata_result = test_video_metadata(video_path)
    
    # 测试视频预处理
    processing_result = test_video_processing(video_path)
    
    # 输出总结
    if metadata_result and processing_result:
        logger.success("所有测试通过")
        return 0
    else:
        logger.error("测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 