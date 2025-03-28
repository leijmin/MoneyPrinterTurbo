import os
import json
import subprocess
import re
from typing import List, Tuple, Optional
from loguru import logger

from app.models import const
from app.models.schema import MaterialInfo, VideoAspect
from app.utils import utils
from app.services.video_metadata import VideoMetadataExtractor


class VideoPreprocessor:
    """
    视频预处理类，专门处理视频素材和图片转视频的功能
    """
    
    @staticmethod
    def get_optimal_scale_mode(v_width: int, v_height: int, 
                              target_width: int, target_height: int) -> Tuple[str, float]:
        """
        根据源视频和目标尺寸，选择最佳的缩放模式
        
        Args:
            v_width: 源视频宽度
            v_height: 源视频高度
            target_width: 目标宽度
            target_height: 目标高度
            
        Returns:
            (缩放滤镜, 缩放比例)
        """
        try:
            # 计算宽高比
            src_ratio = v_width / v_height
            target_ratio = target_width / target_height
            
            # 计算填充和裁剪尺寸
            if src_ratio > target_ratio:
                # 源视频更宽，需要按高度缩放并裁剪宽度
                # 填满高度，裁剪宽度
                scale_h = target_height
                scale_w = int(v_width * (target_height / v_height))
                scale_filter = f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=1"
                
                # 计算需要裁剪的宽度
                crop_x = (scale_w - target_width) // 2
                crop_y = 0
                crop_filter = f",crop={target_width}:{target_height}:{crop_x}:{crop_y}"
                
                scale_ratio = target_height / v_height
            else:
                # 源视频更窄或相等，按宽度缩放并裁剪高度
                # 填满宽度，裁剪高度
                scale_w = target_width
                scale_h = int(v_height * (target_width / v_width))
                scale_filter = f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=1"
                
                # 计算需要裁剪的高度
                crop_x = 0
                crop_y = (scale_h - target_height) // 2
                crop_filter = f",crop={target_width}:{target_height}:{crop_x}:{crop_y}"
                
                scale_ratio = target_width / v_width
            
            # 返回完整的缩放和裁剪滤镜
            return scale_filter + crop_filter, max(scale_ratio, 1.0)
        except Exception as e:
            logger.error(f"计算缩放模式时出错: {str(e)}")
            # 返回一个安全的默认值
            return f"scale={target_width}:{target_height}:force_original_aspect_ratio=1", 1.0
    
    @staticmethod
    def get_video_info(video_path: str) -> Optional[dict]:
        """
        获取视频的完整信息
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含视频信息的字典，如果获取失败则返回None
        """
        try:
            # 确保路径是绝对路径
            abs_path = os.path.abspath(video_path)
            logger.info(f"获取视频信息 - 路径: {abs_path}")
            
            if not os.path.exists(abs_path):
                logger.error(f"视频文件不存在: {abs_path}")
                return None
                
            # 检查文件是否可读
            try:
                with open(abs_path, 'rb') as f:
                    # 只读取前几个字节以验证文件可读
                    f.read(10)
            except Exception as e:
                logger.error(f"无法读取视频文件: {abs_path}, 错误: {str(e)}")
                return None
                
            # 检查文件大小
            file_size = os.path.getsize(abs_path)
            if file_size == 0:
                logger.error(f"视频文件大小为零: {abs_path}")
                return None
            
            logger.info(f"视频文件大小: {file_size} 字节")
                
            # 使用VideoMetadataExtractor获取视频元数据
            metadata = VideoMetadataExtractor.get_video_metadata(abs_path)
            
            # 验证元数据的有效性
            if not metadata:
                logger.error(f"获取视频元数据失败: {abs_path}")
                return None
                
            # 检查关键字段
            if metadata["width"] == 0 or metadata["height"] == 0:
                logger.error(f"无效的视频尺寸: 宽={metadata['width']}, 高={metadata['height']}")
                return None
                
            # 记录获取到的元数据
            logger.info(f"视频元数据: 宽={metadata['width']}, 高={metadata['height']}, " +
                      f"旋转={metadata['rotation']}°, 编码={metadata['codec']}")
                
            return metadata
        except Exception as e:
            logger.error(f"获取视频信息时出错: {str(e)}")
            return None

    @staticmethod
    def preprocess_video_ffmpeg(materials: List[MaterialInfo], clip_duration=4):
        """
        使用ffmpeg预处理视频和图片素材，全部转换为视频
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
        
        Returns:
            处理后的素材列表
        """
        # 预定义目标分辨率 - 竖屏和横屏
        target_portrait_width = 1080
        target_portrait_height = 1920
        target_landscape_width = 1920
        target_landscape_height = 1080
        
        for material in materials:
            if not material.url:
                continue

            ext = utils.parse_extension(material.url)
            
            # 先检查文件是否真的存在
            if not os.path.exists(material.url):
                logger.error(f"文件不存在: {material.url}")
                continue
            
            try:
                if ext in const.FILE_TYPE_VIDEOS:
                    # 获取视频信息
                    video_info = VideoPreprocessor.get_video_info(material.url)
                    if not video_info:
                        logger.error(f"无法获取视频信息: {material.url}")
                        continue
                        
                    # 从视频信息中提取所需数据
                    width = video_info["width"]
                    height = video_info["height"]
                    codec = video_info["codec"]
                    rotation = video_info["rotation"]
                    is_portrait = video_info["is_portrait"]
                    is_4k = video_info["is_4k"]
                    is_hevc = video_info["is_hevc"]
                    is_standard_landscape = video_info["is_standard_landscape"]
                    
                    logger.info(f"视频信息: 宽={width}, 高={height}, 编码={codec}, 旋转={rotation}°")
                    logger.info(f"视频分析 - 是4K: {is_4k}, 是HEVC: {is_hevc}, 标准横屏: {is_standard_landscape}")
                    
                    # 判断视频是否需要处理
                    needs_processing = False
                    
                    # 1. 尺寸太小的视频跳过 (小于480p)
                    if width < 480 or height < 480:
                        logger.warning(f"视频太小，宽: {width}, 高: {height}")
                        continue
                    
                    # 2. 所有非H264视频都需要转码
                    if "h264" not in codec:
                        logger.info(f"非H264编码视频，需要转码: {codec}")
                        needs_processing = True
                    
                    # 3. 所有需要旋转的视频都需要处理
                    if rotation != 0:
                        logger.info(f"视频需要旋转: {rotation}°")
                        needs_processing = True
                    
                    # 4. 尺寸超过目标尺寸的视频需要缩小
                    if width > target_landscape_width or height > target_portrait_height:
                        logger.info(f"视频尺寸超过限制: {width}x{height}")
                        needs_processing = True
                    
                    # 5. 特殊情况：4K HEVC视频的处理
                    is_special_hevc_landscape = False
                    if is_4k and is_hevc and is_standard_landscape and width > height:
                        logger.info("⚠️ 检测到标准横屏4K HEVC视频，尊重其原始方向")
                        is_special_hevc_landscape = True
                        rotation = 0  # 不旋转4K HEVC标准横屏视频
                        needs_processing = True  # 但仍需要处理缩放
                    
                    # 6. 特殊处理：标准横屏且非4K HEVC的视频可能是手机拍摄的竖屏视频
                    if not is_portrait and not is_special_hevc_landscape:
                        if 1.7 < width/height < 1.8 and not is_4k and not is_hevc:  # 接近16:9
                            logger.info("检测到可能是竖屏视频被记录为横屏，添加90度旋转")
                            needs_processing = True
                            rotation = 90  # 强制设置旋转角度
                    
                    # 确定最终的视频方向
                    # 考虑旋转后的有效尺寸
                    effective_width, effective_height = width, height
                    if rotation in [90, 270, -90]:
                        effective_width, effective_height = height, width
                    
                    # 根据有效尺寸确定最终方向
                    is_portrait = effective_height > effective_width
                    
                    # 设置目标尺寸
                    if is_portrait:
                        target_width = target_portrait_width
                        target_height = target_portrait_height
                    else:
                        target_width = target_landscape_width
                        target_height = target_landscape_height
                    
                    if needs_processing:
                        logger.info(f"需要处理的视频: {material.url}")
                        output_path = os.path.join(os.path.dirname(material.url), f"processed_{os.path.basename(material.url)}")
                        
                        # 设置旋转滤镜
                        rotate_filter = ""
                        if rotation == 90:
                            rotate_filter = "transpose=1,"  # 顺时针旋转90度
                            logger.info("应用90度顺时针旋转滤镜")
                        elif rotation == 180:
                            rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                            logger.info("应用180度旋转滤镜")
                        elif rotation == 270 or rotation == -90:
                            rotate_filter = "transpose=2,"  # 逆时针旋转90度（等于顺时针旋转270度）
                            logger.info("应用270度顺时针旋转滤镜")
                        
                        # 应用缩放和裁剪滤镜，确保填充满屏幕
                        v_width = width
                        v_height = height
                        
                        # 考虑旋转后的尺寸
                        if rotation in [90, 270, -90]:
                            v_width, v_height = v_height, v_width
                        
                        # 选择最佳的缩放模式填充目标区域
                        scale_crop_filter, scale_ratio = VideoPreprocessor.get_optimal_scale_mode(
                            v_width, v_height, target_width, target_height
                        )
                        
                        logger.info(f"应用缩放和裁剪: {scale_crop_filter}")
                        
                        # 构建完整的视频滤镜
                        if rotate_filter:
                            vf_filter = f"{rotate_filter}{scale_crop_filter},format=yuv420p"
                        else:
                            vf_filter = f"{scale_crop_filter},format=yuv420p"
                        
                        # 转码命令
                        transcode_cmd = [
                            "ffmpeg", "-y",
                            "-i", material.url,
                            "-c:v", "libx264",
                            "-crf", "23",
                            "-preset", "fast",
                            "-vf", vf_filter,
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-map_metadata", "-1",  # 移除所有元数据
                            "-movflags", "+faststart",  # 添加快速启动标志
                            output_path
                        ]
                        
                        # 记录完整命令和路径信息
                        logger.info(f"执行转码命令: {' '.join(transcode_cmd)}")
                        logger.info(f"输入视频路径: {material.url}")
                        logger.info(f"输出视频路径: {output_path}")
                        
                        # 再次确认输入文件存在
                        if not os.path.exists(material.url):
                            logger.error(f"转码前再次检查：输入视频文件不存在: {material.url}")
                            continue
                            
                        # 确认输出目录存在
                        output_dir = os.path.dirname(output_path)
                        if not os.path.exists(output_dir):
                            logger.info(f"创建输出目录: {output_dir}")
                            try:
                                os.makedirs(output_dir, exist_ok=True)
                            except Exception as e:
                                logger.error(f"创建输出目录失败: {str(e)}")
                                continue
                        
                        # 执行命令并捕获错误
                        try:
                            # 使用subprocess.run而不是Popen，更简单的错误处理
                            result = subprocess.run(
                                transcode_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True,
                                encoding='utf-8',
                                errors='replace',
                                check=False  # 不自动抛出异常
                            )
                            
                            # 检查返回码
                            if result.returncode != 0:
                                logger.error(f"视频处理失败，返回码: {result.returncode}")
                                # 获取完整错误信息
                                stderr_lines = result.stderr.splitlines()
                                # 只记录最后几行，通常包含实际错误
                                for line in stderr_lines[-10:]:
                                    logger.error(f"错误详情: {line.strip()}")
                                continue
                        except Exception as e:
                            logger.error(f"执行转码命令失败: {str(e)}")
                            continue
                        
                        # 确认输出文件存在且大小合理
                        if os.path.exists(output_path):
                            output_size = os.path.getsize(output_path)
                            if output_size > 0:
                                logger.info(f"视频处理成功: {output_path}，文件大小: {output_size} 字节")
                                material.url = output_path
                            else:
                                logger.error(f"视频处理结果文件大小为零: {output_path}")
                                # 尝试删除零大小文件
                                try:
                                    os.remove(output_path)
                                except:
                                    pass
                        else:
                            logger.error(f"视频处理结果文件不存在: {output_path}")
                elif ext in const.FILE_TYPE_IMAGES:
                    logger.info(f"处理图片: {material.url}")
                    # 使用ffmpeg将图片转换为视频，添加缩放效果
                    video_file = f"{material.url}.mp4"
                    
                    # 检查图片是否存在
                    if not os.path.exists(material.url):
                        logger.error(f"图片文件不存在: {material.url}")
                        continue
                        
                    # 检查输出目录是否存在
                    output_dir = os.path.dirname(video_file)
                    if not os.path.exists(output_dir):
                        logger.info(f"创建输出目录: {output_dir}")
                        try:
                            os.makedirs(output_dir, exist_ok=True)
                        except Exception as e:
                            logger.error(f"创建输出目录失败: {str(e)}")
                            continue
                    
                    # 缩放效果：使用zoompan滤镜并确保填充1080x1920的目标区域
                    image_cmd = [
                        "ffmpeg", "-y",
                        "-loop", "1",  # 循环输入
                        "-i", material.url,
                        "-vf", f"zoompan=z='min(zoom+0.0015,1.2)':d={int(clip_duration*30)}:fps=30:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2',scale=1080:1920:force_original_aspect_ratio=1,crop=1080:1920,format=yuv420p",
                        "-c:v", "libx264",
                        "-t", str(clip_duration),
                        "-pix_fmt", "yuv420p",
                        video_file
                    ]
                    
                    # 记录完整命令
                    logger.info(f"执行图片转视频命令: {' '.join(image_cmd)}")
                    
                    try:
                        # 使用更详细的错误处理
                        result = subprocess.run(
                            image_cmd, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.PIPE,
                            universal_newlines=True,
                            encoding='utf-8',
                            errors='replace',
                            check=False
                        )
                        
                        if result.returncode != 0:
                            logger.error(f"图片转视频失败，返回码: {result.returncode}")
                            # 记录错误信息
                            stderr_lines = result.stderr.splitlines()
                            for line in stderr_lines[-10:]:
                                logger.error(f"错误详情: {line.strip()}")
                            continue
                    except Exception as e:
                        logger.error(f"执行图片转视频命令失败: {str(e)}")
                        continue
                    
                    # 检查生成的视频文件
                    if os.path.exists(video_file):
                        file_size = os.path.getsize(video_file)
                        if file_size > 0:
                            material.url = video_file
                            logger.info(f"图片转视频成功: {video_file}，文件大小: {file_size} 字节")
                        else:
                            logger.error(f"生成的视频文件大小为零: {video_file}")
                            # 尝试删除零大小文件
                            try:
                                os.remove(video_file)
                            except:
                                pass
                    else:
                        logger.error(f"图片转视频失败，输出文件不存在: {video_file}")
                else:
                    logger.warning(f"不支持的文件类型: {material.url}")
                    continue
                    
            except Exception as e:
                logger.error(f"处理素材失败: {material.url}, 错误: {str(e)}")
                continue
                
        return materials

    @staticmethod
    def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
        """
        处理视频和图片素材，是preprocess_video_ffmpeg的便捷包装
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
            
        Returns:
            处理后的素材列表
        """
        return VideoPreprocessor.preprocess_video_ffmpeg(materials, clip_duration)

