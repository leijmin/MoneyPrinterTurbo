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
from app.services.video_encoder import HardwareAccelerator , EncoderConfig



class VideoPreprocessor:
    """
    视频预处理类，专门处理视频素材和图片转视频的功能
    """
    
    @staticmethod
    def get_optimal_scale_mode(v_width: int, v_height: int, 
                              target_width: int, target_height: int) -> Tuple[str, float]:
        """
        根据源视频和目标尺寸，选择最佳的缩放模式，增强版本
        
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
            
            # 检查源尺寸是否小于目标尺寸，这种情况下不能使用裁剪，只能使用填充
            if v_width < target_width or v_height < target_height:
                logger.info(f"源视频尺寸({v_width}x{v_height})小于目标尺寸({target_width}x{target_height})，使用填充模式")
                # 使用填充模式，先缩放保持比例，然后填充黑边
                # 计算等比例缩放的尺寸
                scale_ratio = min(target_width / v_width, target_height / v_height)
                scaled_width = int(v_width * scale_ratio)
                scaled_height = int(v_height * scale_ratio)
                
                # 计算填充位置（居中）
                pad_x = (target_width - scaled_width) // 2
                pad_y = (target_height - scaled_height) // 2
                
                # 构建滤镜：缩放后填充
                scale_filter = f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=1"
                pad_filter = f",pad={target_width}:{target_height}:{pad_x}:{pad_y}:color=black"
                
                return scale_filter + pad_filter, scale_ratio
            
            # 标准处理逻辑（源尺寸大于目标尺寸的情况）
            if src_ratio > target_ratio:
                # 源视频更宽，需要按高度缩放并裁剪宽度
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
            # 返回一个安全的默认值 - 使用填充模式避免裁剪错误
            return f"scale='min({target_width},iw)':'min({target_height},ih)':force_original_aspect_ratio=1,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black", 1.0
    
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
        使用ffmpeg预处理视频和图片素材
        
        Args:
            materials: 素材信息列表
            clip_duration: 图片转视频的持续时间（秒）
        
        Returns:
            处理后的素材列表
        """
        # 安全检查：确保材料是有效的列表
        if materials is None:
            logger.warning("材料列表为空")
            return []
        
        # 处理非列表类型的错误参数情况
        if not isinstance(materials, list):
            logger.warning(f"无效的材料参数: {materials}")
            return []
        
        # 预定义目标分辨率 - 竖屏和横屏
        target_portrait_width = 1080
        target_portrait_height = 1920
        target_landscape_width = 1920
        target_landscape_height = 1080
        
        for material in materials:
            # 检查是否是有效的MaterialInfo对象
            if not hasattr(material, 'url') or not material.url:
                logger.warning(f"无效的素材对象: {material}")
                continue

            # 检查文件是否存在
            if not os.path.exists(material.url):
                logger.error(f"文件不存在: {material.url}")
                continue
            
            ext = utils.parse_extension(material.url)
            
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
                    
                    # 检测GPU加速器
                    encoder = HardwareAccelerator.get_optimal_encoder()
                    logger.info(f"使用编码器: {encoder}")
                    
                    # 处理视频逻辑...
                    # [其余代码保持不变]
                    
                elif ext in const.FILE_TYPE_IMAGES:
                    # 图片处理逻辑...
                    # [其余代码保持不变]
                    
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

    def preprocess_video_ffmpeg(self, materials, clip_duration=4):
        """使用ffmpeg处理视频(优化版)"""
        # 参数验证
        if materials is None:
            logger.warning("材料列表为空")
            return []
        
        if not isinstance(materials, list):
            logger.warning(f"无效的材料参数: {materials}")
            return []
        
        # 计数器
        processed_count = 0
        failed_count = 0
        skipped_count = 0
        
        # 获取编码器
        encoder = HardwareAccelerator.get_optimal_encoder()
        logger.info(f"使用编码器: {encoder}")
        
        for idx, material in enumerate(materials):
            if not hasattr(material, 'url') or not material.url:
                logger.warning(f"跳过无效素材: {material}")
                skipped_count += 1
                continue
            
            if not os.path.exists(material.url):
                logger.warning(f"素材文件不存在: {material.url}")
                skipped_count += 1
                continue
            
            try:
                logger.info(f"处理视频 [{idx+1}/{len(materials)}]: {os.path.basename(material.url)}")
                
                # 处理文件
                ext = utils.parse_extension(material.url)
                
                if ext in const.FILE_TYPE_VIDEOS:
                    # 获取视频信息
                    video_info = self.get_video_info(material.url)
                    if not video_info:
                        logger.error(f"无法获取视频信息: {material.url}")
                        failed_count += 1
                        continue
                    
                    # 判断是否需要处理
                    if self._process_video_file(material, video_info, encoder):
                        processed_count += 1
                    else:
                        failed_count += 1
                    
                elif ext in const.FILE_TYPE_IMAGES:
                    # 处理图片转视频
                    if self._process_image_file(material, clip_duration):
                        processed_count += 1
                    else:
                        failed_count += 1
                else:
                    logger.warning(f"不支持的文件类型: {material.url}")
                    skipped_count += 1
                
            except Exception as e:
                logger.error(f"处理素材出错: {str(e)}")
                failed_count += 1
        
        # 处理结果统计
        logger.info(f"视频处理完成: 总计{len(materials)}个, 成功{processed_count}个, "
                    f"失败{failed_count}个, 跳过{skipped_count}个")
        
        return materials

    def _is_valid_material(self, material):
        """检查材料是否有效"""
        if not material.url or not os.path.exists(material.url):
            logger.warning(f"无效的视频路径: {getattr(material, 'url', '未设置')}")
            return False
        return True
    
    def _process_single_video(self, material, clip_duration, encoder):
        """处理单个视频文件"""
        try:
            # 1. 提取视频信息
            video_path = material.url
            video_info = self._extract_video_info(video_path)
            
            if not video_info:
                logger.error(f"无法获取有效的视频信息: {os.path.basename(video_path)}")
                return False
            
            # 2. 判断是否需要处理
            process_config = self._determine_processing_needs(video_info, video_path)
            
            # 如果不需要处理，直接返回成功
            if not process_config["needs_processing"]:
                logger.info(f"视频无需处理: {os.path.basename(video_path)}")
                return True
            
            # 3. 执行视频处理
            return self._execute_video_processing(material, video_info, process_config, encoder, clip_duration)
        
        except Exception as e:
            logger.error(f"视频处理过程中出错: {str(e)}")
            import traceback
            logger.debug(f"错误详情: {traceback.format_exc()}")
            return False

    def _extract_video_info(self, video_path):
        """提取视频元数据信息"""
        try:
            video_info = self.video_metadata_extractor.get_video_info(video_path)
            
            # 检查视频信息是否有效
            if not video_info or "width" not in video_info or "height" not in video_info:
                logger.error(f"无法获取视频信息: {os.path.basename(video_path)}")
                return None
            
            # 日志记录视频信息
            width = video_info["width"]
            height = video_info["height"]
            rotation = video_info.get("rotation", 0)
            codec = video_info.get("codec", "unknown")
            
            logger.info(f"视频信息: {width}x{height}, 旋转:{rotation}°, 编码:{codec}")
            return video_info
        
        except Exception as e:
            logger.error(f"提取视频信息失败: {str(e)}")
            return None

    def _determine_processing_needs(self, video_info, file_path):
        """确定视频处理需求"""
        # 提取视频基本信息
        width = int(video_info.get("width", 0))
        height = int(video_info.get("height", 0))
        rotation = int(video_info.get("rotation", 0))
        codec = str(video_info.get("codec", "unknown"))
        
        # 文件扩展名
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        # 如果视频维度无效，不处理
        if width <= 0 or height <= 0:
            logger.warning(f"视频尺寸无效: {width}x{height}")
            return {"needs_processing": False}
        
        # 初始化配置
        config = {
            "needs_processing": False,
            "needs_rotation": False,
            "needs_scaling": False,
            "use_metadata_rotation": False,
            "target_width": width,
            "target_height": height,
            "is_4k": width * height >= 3840 * 2160,
            "is_high_quality": width >= 1920 or height >= 1920
        }
        
        # 判断是否为标准横屏视频(不考虑旋转)
        is_standard_landscape = (width > height)
        
        # 1. 检查编码是否需要转换
        if codec.lower() not in ["h264", "avc"]:
            config["needs_processing"] = True
            logger.info(f"需要转换编码: {codec} -> h264")
        
        # 2. 检查旋转是否需要处理
        if rotation != 0:
            # 标准化旋转角度为90的倍数
            normalized_rotation = ((rotation + 45) // 90 * 90) % 360
            video_info["rotation"] = normalized_rotation  # 更新为标准角度
            
            # 高质量视频优先使用元数据旋转
            if config["is_high_quality"]:
                config["use_metadata_rotation"] = True
                logger.info(f"对高质量视频使用元数据旋转: {normalized_rotation}°")
            else:
                config["needs_rotation"] = True
                config["needs_processing"] = True
                logger.info(f"需要物理旋转视频: {normalized_rotation}°")
        
        # 3. 特殊处理：所有高分辨率视频的特殊处理
        is_high_res = width * height >= 3840 * 2160 or width >= 1920 or height >= 1920
        
        if is_high_res and is_standard_landscape:
            logger.info("检测到高分辨率标准横屏视频，采用专业处理")
            
            # 对于所有高分辨率视频都采用同样的处理方式
            if rotation != 0:
                if "mov" in ext.lower() or "mp4" in ext.lower():
                    logger.info(f"高分辨率视频旋转处理: {rotation}°")
                    # AVC编码优先使用物理旋转确保兼容性
                    if "avc" in codec.lower() or "h264" in codec.lower():
                        config["needs_rotation"] = True
                        config["use_metadata_rotation"] = False
                    else:
                        # HEVC继续使用元数据旋转
                        config["use_metadata_rotation"] = True
                        config["needs_rotation"] = False
        
        # 4. 检查尺寸是否需要调整
        max_width = 1920
        max_height = 1920
        
        if width > max_width or height > max_height:
            config["needs_scaling"] = True
            config["needs_processing"] = True
            
            # 保持宽高比
            scale_ratio = min(max_width / width, max_height / height)
            config["target_width"] = int(width * scale_ratio)
            config["target_height"] = int(height * scale_ratio)
            
            logger.info(f"需要调整尺寸: {width}x{height} -> {config['target_width']}x{config['target_height']}")
        
        return config

    def _execute_video_processing(self, material, video_info, config, encoder, clip_duration):
        """执行视频处理"""
        source_path = material.url
        output_dir = os.path.dirname(source_path)
        file_name = os.path.basename(source_path)
        base_name, ext = os.path.splitext(file_name)
        processed_path = os.path.join(output_dir, f"{base_name}_processed{ext}")
        
        # 根据配置构建滤镜字符串
        vf_filter = self._build_filter_string(video_info, config)
        
        # 获取硬件加速参数
        input_params = HardwareAccelerator.optimize_input_parameters(encoder, source_path)
        
        # 获取编码器参数(根据目标尺寸)
        encoder_params = EncoderConfig.get_encoder_params(
            encoder, 
            config["target_width"], 
            config["target_height"]
        )
        
        # 构建FFMPEG命令
        cmd = self._build_ffmpeg_command(
            source_path, 
            processed_path,
            encoder,
            vf_filter,
            input_params,
            encoder_params,
            config
        )
        
        # 执行命令
        success = self._run_ffmpeg_command(cmd)
        
        # 如果失败，尝试简化命令重试
        if not success:
            logger.warning(f"使用{encoder}处理视频失败，尝试CPU编码...")
            # 简化滤镜和编码器
            simple_filter = self._build_simple_filter(video_info, config)
            simple_cmd = self._build_ffmpeg_command(
                source_path, 
                processed_path,
                "libx264",  # 使用软件编码器
                simple_filter,
                [],  # 不使用硬件加速
                {"preset": "medium", "crf": "23"},  # 简单编码参数
                config
            )
            success = self._run_ffmpeg_command(simple_cmd)
        
        # 如果仍然失败，尝试直接复制流
        if not success:
            logger.warning("简化编码也失败，尝试直接流复制...")
            copy_cmd = [
                "ffmpeg", "-y",
                "-i", source_path,
                "-c:v", "copy",
                "-c:a", "copy",
                processed_path
            ]
            success = self._run_ffmpeg_command(copy_cmd)
        
        # 处理成功后，更新材料路径
        if success and os.path.exists(processed_path) and os.path.getsize(processed_path) > 0:
            # 获取处理后视频的信息以验证
            processed_info = self._extract_video_info(processed_path)
            
            if processed_info and processed_info.get("duration", 0) > 0:
                material.url = processed_path
                logger.success(f"视频处理成功: {os.path.basename(processed_path)}")
                return True
            else:
                logger.error(f"处理后的视频无效: {os.path.basename(processed_path)}")
                return False
        else:
            logger.error(f"视频处理失败: {os.path.basename(source_path)}")
            return False

    def _build_filter_string(self, video_info, config):
        """构建视频滤镜字符串"""
        filters = []
        
        # 1. 旋转滤镜(如果需要)
        if config["needs_rotation"] and not config["use_metadata_rotation"]:
            rotation = video_info.get("rotation", 0)
            # 标准化旋转角度
            normalized_rotation = ((rotation + 45) // 90 * 90) % 360
            
            rotation_filters = {
                90: "transpose=1",     # 顺时针90度
                180: "transpose=2,transpose=2",  # 180度
                270: "transpose=2"     # 逆时针90度(相当于顺时针270度)
            }
            
            if normalized_rotation in rotation_filters:
                filters.append(rotation_filters[normalized_rotation])
        
        # 2. 缩放滤镜(如果需要)
        if config["needs_scaling"]:
            scale_filter = f"scale={config['target_width']}:{config['target_height']}:flags=lanczos"
            filters.append(scale_filter)
        
        # 3. 确保输出为yuv420p格式(兼容性更好)
        filters.append("format=yuv420p")
        
        # 组合所有滤镜
        return ",".join(filters) if filters else "null"

    def _build_ffmpeg_command(self, input_path, output_path, encoder, vf_filter, input_params, encoder_params, config):
        """构建FFMPEG命令"""
        cmd = ["ffmpeg", "-y"]
        
        # 添加输入参数
        cmd.extend(input_params)
        
        # 添加输入文件
        cmd.extend(["-i", input_path])
        
        # 添加视频滤镜
        if vf_filter and vf_filter != "null":
            cmd.extend(["-vf", vf_filter])
        
        # 如果使用元数据旋转，添加特殊参数
        if config["use_metadata_rotation"]:
            # 复制视频流中的元数据
            cmd.extend(["-metadata:s:v:0", "rotate=0"])
        
        # 添加视频编码器
        cmd.extend(["-c:v", encoder])
        
        # 提取并添加编码器特定参数
        bitrate = encoder_params.pop("bitrate", None)
        maxrate = encoder_params.pop("maxrate", None)
        bufsize = encoder_params.pop("bufsize", None)
        
        if bitrate:
            cmd.extend(["-b:v", f"{bitrate}k"])
        if maxrate:
            cmd.extend(["-maxrate", f"{maxrate}k"])
        if bufsize:
            cmd.extend(["-bufsize", f"{bufsize}k"])
        
        # 添加其他编码器参数
        for key, value in encoder_params.items():
            cmd.extend([f"-{key}", str(value)])
        
        # 复制音频流
        cmd.extend(["-c:a", "copy"])
        
        # 添加输出文件
        cmd.append(output_path)
        
        return cmd

    def _run_ffmpeg_command(self, cmd):
        """执行FFMPEG命令并处理输出"""
        try:
            logger.info(f"执行命令: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 收集完整错误输出
            stderr_output = []
            
            # 实时显示处理进度
            for line in process.stderr:
                stderr_output.append(line)
                if "time=" in line and "bitrate=" in line:
                    logger.debug(f"进度: {line.strip()}")
            
            process.wait()
            
            if process.returncode != 0:
                logger.error("命令执行失败，错误详情:")
                for line in stderr_output:
                    if "Error" in line or "Invalid" in line or "failed" in line:
                        logger.error(line.strip())
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"执行命令时出错: {str(e)}")
            return False

    def _build_simple_filter(self, width, height, target_width, target_height):
        """构建安全的视频滤镜"""
        # 避免无效的尺寸
        if width <= 0: width = 1
        if height <= 0: height = 1
        
        # 使用安全的填充模式
        return (f"scale=w='min({target_width},iw)':h='min({target_height},ih)':"
                f"force_original_aspect_ratio=1,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"format=yuv420p")

