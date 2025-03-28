import subprocess
import os
import threading
import shutil
import time
import logging

logger = logging.getLogger(__name__)

class EncoderConfig:
    """编码器配置类"""
    @staticmethod
    def get_optimal_bitrate(width: int, height: int, is_4k: bool = False) -> int:
        """计算最优码率（kbps）"""
        pixel_count = width * height
        
        # 超高清视频 (4K及以上)
        if is_4k:
            base_bitrate = 30000  # 30Mbps基准
        # 高清视频 (1080p-2K)
        elif pixel_count >= 1920 * 1080:
            base_bitrate = 15000  # 15Mbps基准
        # 标清视频
        else:
            base_bitrate = 8000   # 8Mbps基准
            
        # 根据像素数精确调整码率
        adjusted_bitrate = int((pixel_count / (1920 * 1080)) * base_bitrate)
        
        # 设置合理的下限和上限
        min_bitrate = 6000   # 最低保证6Mbps
        max_bitrate = 40000  # 最高不超过40Mbps
        
        # 确保码率在合理范围内
        return max(min_bitrate, min(adjusted_bitrate, max_bitrate))
    
    @staticmethod
    def get_encoder_params(hw_accel: str, width: int, height: int) -> dict:
        """获取编码器参数"""
        is_4k = width * height >= 3840 * 2160
        bitrate = EncoderConfig.get_optimal_bitrate(width, height, is_4k)
        
        # 基础参数
        params = {
            "bitrate": bitrate,
            "maxrate": int(bitrate * 1.5),
            "bufsize": int(bitrate * 3),  # 增大缓冲区
            "refs": 6 if is_4k else 5,    # 增加参考帧数量
            "g": 30,  # GOP大小
        }
        
        # 根据不同硬件加速器优化参数
        if hw_accel == "h264_nvenc":
            # 根据英伟达GPU代际优化参数
            try:
                # 获取GPU信息
                gpu_info_cmd = ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
                gpu_name = subprocess.check_output(gpu_info_cmd, universal_newlines=True).strip()
                
                # 针对RTX 30/40系列或更高级GPU优化
                if any(x in gpu_name.upper() for x in ["RTX 30", "RTX 40", "A100", "A6000"]):
                    params.update({
                        "preset": "p7",     # 最高质量预设
                        "tune": "hq",       # 高质量调优
                        "multipass": "fullres",  # 两遍编码模式
                        "spatial-aq": "1",  # 空间自适应量化
                        "temporal-aq": "1", # 时间自适应量化
                        "aq-strength": "15", # 高自适应量化强度
                        "rc-lookahead": "40", # 增加前瞻帧数
                        "surfaces": "64",   # 增加表面缓冲区
                        "gpu": "0",         # 指定GPU索引
                        "b_ref_mode": "middle" # 使用B帧作为参考
                    })
                else:
                    # 老旧GPU优化
                    params.update({
                        "preset": "p6",     # 平衡预设
                        "rc-lookahead": "24"  # 减少前瞻帧数避免过载
                    })
            except Exception:
                # 默认NVENC配置
                params.update({
                    "preset": "p7",
                    "tune": "hq"
                })
        elif hw_accel == "h264_qsv":
            params.update({
                "preset": "veryslow", # 最慢压缩 = 最高质量
                "look_ahead": "1",
                "global_quality": 15, # 高质量参数
                "profile:v": "high"
                # 移除level参数
            })
        elif hw_accel == "h264_amf":
            params.update({
                "quality": "quality",
                "profile:v": "high",
                # 移除level参数
                "refs": 6 if is_4k else 5,  # 增加参考帧
                "preanalysis": "1",
                "vbaq": "1",  # 启用方差基础自适应量化
                "enforce_hrd": "1"
                # 移除可能引起问题的参数
            })
        else:  # libx264软件编码
            params.update({
                "preset": "slow",  # 慢速预设，提高质量
                "crf": "18",       # 高质量CRF值
                "profile:v": "high",
                # 移除level参数
                "refs": 5,         # 参考帧数量
                "psy": "1",        # 启用心理视觉优化
                "psy-rd": "1.0:0.05"  # 心理视觉优化率失真
            })
        
        # 为4K视频添加额外参数
        if is_4k:
            # 增加4K视频的颜色属性和质量设置
            extra_params = {
                "pix_fmt": "yuv420p10le" if hw_accel == "libx264" else "yuv420p", # 10-bit色深(仅软件编码)
                "colorspace": "bt709",     # 标准色彩空间
                "color_primaries": "bt709", # 色彩原色
                "color_trc": "bt709",       # 色彩传输特性
                "movflags": "+faststart"    # 文件元数据前置，便于快速播放
            }
            params.update(extra_params)
        
        return params

class HardwareAccelerator:
    """硬件加速检测与配置类"""
    _ENCODERS_CACHE = None  # 静态缓存
    
    @staticmethod
    def detect_available_encoders(force_refresh=False):
        """检测系统支持的硬件加速器"""
        # 使用缓存避免重复检测
        if HardwareAccelerator._ENCODERS_CACHE is not None and not force_refresh:
            return HardwareAccelerator._ENCODERS_CACHE
            
        # 一次性检查所有编码器
        encoders = {"nvidia": False, "intel": False, "amd": False}
        encoder_names = {"nvidia": "h264_nvenc", "intel": "h264_qsv", "amd": "h264_amf"}
        
        try:
            ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-encoders"]
            output = subprocess.check_output(ffmpeg_cmd, universal_newlines=True)
            
            # 检查所有支持的编码器
            for vendor, encoder in encoder_names.items():
                if encoder in output:
                    encoders[vendor] = True
                    logger.info(f"✅ 检测到{vendor.upper()}硬件加速支持")
            
            # 未检测到任何GPU加速器
            if not any(encoders.values()):
                logger.info("⚠️ 未检测到GPU加速支持，将使用CPU编码")
        except Exception as e:
            logger.warning(f"⚠️ 检测硬件加速器失败: {str(e)}")
        
        # 缓存结果
        HardwareAccelerator._ENCODERS_CACHE = encoders
        return encoders
    
    @staticmethod
    def get_optimal_encoder(preferred_gpu="nvidia"):
        """获取最优的编码器"""
        encoders = HardwareAccelerator.detect_available_encoders()
        encoder_map = {"nvidia": "h264_nvenc", "intel": "h264_qsv", "amd": "h264_amf"}
        
        # 首选用户指定的GPU
        if preferred_gpu.lower() in encoder_map and encoders[preferred_gpu.lower()]:
            return encoder_map[preferred_gpu.lower()]
        
        # 按优先级选择
        for vendor in ["nvidia", "intel", "amd"]:
            if encoders[vendor]:
                return encoder_map[vendor]
        
        # 无GPU支持，使用CPU编码
        return "libx264"
    
    @staticmethod
    def optimize_input_parameters(encoder, input_path):
        """为不同的加速器优化输入参数"""
        params = {
            "h264_nvenc": ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
            "h264_qsv": ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"],
            "h264_amf": ["-hwaccel", "d3d11va"],
            "libx264": []  # CPU编码不需要特殊参数
        }
        
        # 获取基本参数
        input_params = params.get(encoder, [])
        
        # NVIDIA特殊处理
        if encoder == "h264_nvenc" and (input_path.lower().endswith((".mp4", ".mov"))):
            input_params.extend(["-hwaccel_device", "0"])
        
        return input_params

