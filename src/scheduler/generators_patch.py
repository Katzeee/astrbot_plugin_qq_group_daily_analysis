def generate_topics_text_summary(topics: list, current_date: str) -> list[str]:
    """生成话题总结文本，每5条一组
    
    Args:
        topics: 话题列表
        current_date: 当前日期字符串（格式：YYYY年MM月DD日）
    
    Returns:
        话题总结文本列表，每个元素包含最多5条话题
    """
    if not topics:
        return []
    
    summaries = []
    for i in range(0, len(topics), 5):
        batch = topics[i:i+5]
        text = f"📅 {current_date}\n\n💬 热门话题总结\n\n"
        for idx, topic in enumerate(batch, start=i+1):
            contributors_str = "、".join(topic.contributors)
            text += f"{idx}. {topic.topic}\n"
            text += f"   参与者: {contributors_str}\n"
            text += f"   {topic.detail}\n\n"
        summaries.append(text)
    
    return summaries