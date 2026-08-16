"""RapidOCR 关键帧文字识别"""

from pathlib import Path

from loguru import logger


class OCRProcessor:
    """基于 RapidOCR 的图片文字提取器，自动过滤低置信度结果。"""

    def __init__(self, confidence_threshold: float = 0.5) -> None:
        self.confidence_threshold = confidence_threshold
        self._engine = None

    def _load_engine(self) -> None:
        """延迟加载 OCR 引擎。"""
        if self._engine is not None:
            return
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        logger.info("RapidOCR 引擎加载完成")

    def extract_text(self, image_path: str) -> str:
        """从单张图片中提取文字，返回拼接后的纯文本。"""
        self._load_engine()

        img = Path(image_path)
        if not img.exists():
            logger.warning("图片不存在: {}", img)
            return ""

        result, _ = self._engine(str(img))

        if result is None:
            logger.debug("OCR 无结果: {}", img.name)
            return ""

        texts: list[str] = []
        for item in result:
            # item 格式: [bbox, text, confidence]
            text = item[1]
            confidence = item[2]
            if confidence >= self.confidence_threshold:
                texts.append(text)

        joined = " ".join(texts)
        logger.debug("OCR 提取: {} → {} 字", img.name, len(joined))
        return joined

    def batch_extract(self, image_paths: list[str]) -> list[str]:
        """批量提取多张图片的文字。"""
        results: list[str] = []
        for path in image_paths:
            text = self.extract_text(path)
            results.append(text)
        total_chars = sum(len(t) for t in results)
        logger.info("批量 OCR 完成: {} 张图片, 共 {} 字", len(image_paths), total_chars)
        return results
