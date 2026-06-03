import re

def extract_title_from_text(text: str) -> str:
    """智能提纯名称：剥离链接、访问码、指引等无关文本，抽取核心剧名"""
    if not text:
        return "未知资源"
        
    # 移除链接
    text = re.sub(r'https?://[^\s]+', '', text)
    # 移除常见的废话前缀/后缀
    text = re.sub(r'(?:码|密码|提取码|访问码)[:：\s]*[a-zA-Z0-9]+(?:\b|$)', '', text)
    text = re.sub(r'复制这段内容.*直接打开！', '', text)
    text = re.sub(r'提取码：[a-zA-Z0-9]+', '', text)
    
    # 逐行读取，寻找第一行有效的中文字符串
    lines = text.split('\n')
    for line in lines:
        cleaned = line.strip()
        # 移除行首尾的标点符号
        cleaned = re.sub(r'^[#\*【\[]+|[】\]\*#]+$', '', cleaned).strip()
        if len(cleaned) > 1:
            return cleaned
            
    return "未知资源"
