import subprocess
import os
import threading
import shutil
import time
import logging
import re

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
    def test_encoder(encoder):
        """测试编码器是否实际可用"""
        test_cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=30", 
            "-c:v", encoder, "-frames:v", "1", "-f", "null", "-"
        ]
        
        try:
            result = subprocess.run(
                test_cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False
            )
            
            if result.returncode == 0:
                logger.info(f"✓ 编码器 {encoder} 测试通过")
                return True
            
            # 检查错误消息
            if "No such filter" in result.stderr or "Unknown encoder" in result.stderr:
                logger.warning(f"编码器 {encoder} 虽然被ffmpeg列出，但实际不可用")
                return False
            
            # 检查是否需要特殊参数
            if "Invalid argument" in result.stderr and "nvenc" in encoder:
                logger.warning(f"编码器 {encoder} 需要特殊配置才能使用")
                # 这里暂时返回True，因为我们会通过sanitize_gpu_params进一步优化
                return True
            
            logger.warning(f"编码器 {encoder} 测试失败: {result.stderr[:100]}...")
            return False
        except Exception as e:
            logger.warning(f"测试编码器 {encoder} 时出错: {str(e)}")
            return False
    
    @staticmethod
    def diagnose_gpu_issues():
        """诊断GPU相关问题并提供详细报告"""
        diagnostic_report = {
            "nvidia_driver": False,
            "nvidia_gpu_found": False,
            "ffmpeg_nvenc_support": False,
            "nvenc_functional": False,
            "recommended_encoder": "libx264",
            "issues": [],
            "solutions": []
        }
        
        # 1. 检查NVIDIA驱动
        try:
            smi_result = subprocess.run(
                ["nvidia-smi"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False
            )
            
            if smi_result.returncode == 0:
                diagnostic_report["nvidia_driver"] = True
                diagnostic_report["nvidia_gpu_found"] = True
                logger.info("✅ NVIDIA驱动正常")
                
                # 提取GPU型号
                gpu_info_cmd = ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
                gpu_name = subprocess.check_output(gpu_info_cmd, universal_newlines=True).strip()
                logger.info(f"检测到GPU: {gpu_name}")
                
                # 检查CUDA版本
                if "CUDA Version:" in smi_result.stdout:
                    cuda_version = re.search(r"CUDA Version: ([0-9.]+)", smi_result.stdout)
                    if cuda_version:
                        logger.info(f"CUDA版本: {cuda_version.group(1)}")
            else:
                diagnostic_report["issues"].append("NVIDIA驱动未安装或未正常运行")
                diagnostic_report["solutions"].append("请安装或更新NVIDIA显卡驱动")
                logger.warning("❌ NVIDIA驱动未找到或无法运行")
        except Exception as e:
            diagnostic_report["issues"].append(f"检查NVIDIA驱动时出错: {str(e)}")
            logger.warning(f"检查NVIDIA驱动时出错: {str(e)}")
        
        # 2. 检查ffmpeg的NVENC支持
        try:
            ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-encoders"]
            output = subprocess.check_output(ffmpeg_cmd, universal_newlines=True)
            
            if "h264_nvenc" in output:
                diagnostic_report["ffmpeg_nvenc_support"] = True
                logger.info("✅ ffmpeg支持NVENC编码")
            else:
                diagnostic_report["issues"].append("ffmpeg不支持NVENC")
                diagnostic_report["solutions"].append("请使用包含NVENC支持的ffmpeg版本")
                logger.warning("❌ ffmpeg不支持NVENC")
        except Exception as e:
            diagnostic_report["issues"].append(f"检查ffmpeg时出错: {str(e)}")
            logger.warning(f"检查ffmpeg时出错: {str(e)}")
        
        # 3. 测试NVENC功能性
        if diagnostic_report["nvidia_driver"] and diagnostic_report["ffmpeg_nvenc_support"]:
            # 测试简单编码
            test_cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=30", 
                "-c:v", "h264_nvenc", "-preset", "p1", "-frames:v", "1", 
                "-f", "null", "-"
            ]
            
            try:
                result = subprocess.run(
                    test_cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    check=False
                )
                
                if result.returncode == 0:
                    diagnostic_report["nvenc_functional"] = True
                    diagnostic_report["recommended_encoder"] = "h264_nvenc"
                    logger.info("✅ NVENC编码器测试通过")
                else:
                    error_msg = result.stderr
                    logger.warning(f"❌ NVENC编码器测试失败: {error_msg}")
                    
                    # 分析错误原因
                    if "Cannot load nvcuda.dll" in error_msg:
                        diagnostic_report["issues"].append("无法加载CUDA库")
                        diagnostic_report["solutions"].append("请安装或更新CUDA")
                    elif "Generic error in an external library" in error_msg:
                        diagnostic_report["issues"].append("NVENC外部库错误，可能是驱动与ffmpeg不兼容")
                        diagnostic_report["solutions"].append("尝试更新NVIDIA驱动到最新版本")
                    elif "No NVENC capable devices found" in error_msg:
                        diagnostic_report["issues"].append("未找到支持NVENC的设备")
                        diagnostic_report["solutions"].append("您的GPU可能不支持NVENC编码")
                    else:
                        diagnostic_report["issues"].append(f"NVENC测试失败: {error_msg[:100]}")
                        diagnostic_report["solutions"].append("尝试使用简化编码参数")
            except Exception as e:
                diagnostic_report["issues"].append(f"测试NVENC时出错: {str(e)}")
                logger.warning(f"测试NVENC时出错: {str(e)}")
        
        # 输出诊断总结
        logger.info("=== GPU诊断报告 ===")
        logger.info(f"NVIDIA驱动: {'正常' if diagnostic_report['nvidia_driver'] else '不可用'}")
        logger.info(f"NVIDIA GPU: {'已找到' if diagnostic_report['nvidia_gpu_found'] else '未找到'}")
        logger.info(f"ffmpeg NVENC支持: {'支持' if diagnostic_report['ffmpeg_nvenc_support'] else '不支持'}")
        logger.info(f"NVENC功能: {'正常' if diagnostic_report['nvenc_functional'] else '不可用'}")
        logger.info(f"推荐编码器: {diagnostic_report['recommended_encoder']}")
        
        if diagnostic_report["issues"]:
            logger.info("发现的问题:")
            for i, issue in enumerate(diagnostic_report["issues"]):
                logger.info(f"  {i+1}. {issue}")
            
            logger.info("建议解决方案:")
            for i, solution in enumerate(diagnostic_report["solutions"]):
                logger.info(f"  {i+1}. {solution}")
        
        return diagnostic_report
    
    @staticmethod
    def get_optimal_encoder(preferred_gpu="nvidia", force_diagnostic=False):
        """获取最优的编码器，并确保它实际可用"""
        # 如果明确要求诊断，执行完整GPU诊断
        if force_diagnostic:
            diagnostic = HardwareAccelerator.diagnose_gpu_issues()
            return diagnostic["recommended_encoder"]
        
        encoders = HardwareAccelerator.detect_available_encoders()
        encoder_map = {"nvidia": "h264_nvenc", "intel": "h264_qsv", "amd": "h264_amf"}
        
        # 首选用户指定的GPU
        if preferred_gpu.lower() in encoder_map and encoders[preferred_gpu.lower()]:
            encoder = encoder_map[preferred_gpu.lower()]
            
            # 使用更简单的NVENC测试，避免复杂参数
            if encoder == "h264_nvenc":
                # 使用简化参数测试NVENC
                test_cmd = [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=30", 
                    "-c:v", "h264_nvenc", "-preset", "p1", "-frames:v", "1", 
                    "-f", "null", "-"
                ]
                
                try:
                    result = subprocess.run(
                        test_cmd, 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        check=False
                    )
                    
                    if result.returncode == 0:
                        logger.info(f"✅ NVENC编码器测试通过（简化参数）")
                        return encoder
                    else:
                        # 输出详细错误信息帮助诊断
                        logger.warning(f"❌ NVENC编码器测试失败:")
                        logger.warning(result.stderr[:500])
                        
                        # 如果遇到特定错误，尝试执行完整诊断
                        if "Generic error in an external library" in result.stderr:
                            logger.warning("检测到NVENC外部库错误，执行完整诊断...")
                            HardwareAccelerator.diagnose_gpu_issues()
                except Exception as e:
                    logger.warning(f"测试NVENC时出错: {str(e)}")
            else:
                # 其他GPU编码器的测试
                if HardwareAccelerator.test_encoder(encoder):
                    logger.info(f"使用GPU加速器: {encoder}")
                    return encoder
        
        # 按优先级尝试其他可用GPU
        for vendor in ["nvidia", "intel", "amd"]:
            if vendor != preferred_gpu.lower() and encoders[vendor]:
                encoder = encoder_map[vendor]
                if HardwareAccelerator.test_encoder(encoder):
                    logger.info(f"使用备选GPU加速器: {encoder}")
                    return encoder
        
        # 无GPU支持，使用CPU编码
        logger.info("使用CPU软件编码(libx264)")
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

