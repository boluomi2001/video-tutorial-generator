---
title: "{{ title }}"
author: "{{ author }}"
date: "{{ date }}"
category: "{{ category }}"
tags:
{% for tag in tags %}  - {{ tag }}
{% endfor %}
source: "微信视频号收藏"
---

# {{ title }}

> [!summary] 摘要
> {{ summary }}

{% if visual_observations %}
> [!eye] 画面观察
> {{ visual_observations }}
{% endif %}

## 📌 核心要点

{% for point in key_points %}
- {{ point }}
{% endfor %}

## 🔗 资源与工具

{% for resource in resources %}
- {{ resource }}
{% endfor %}

## ✅ 可行动建议

### 💻 开发应用
{% for item in action_items.dev %}
- [ ] {{ item | replace("\n", " ") }}
{% endfor %}

### 🏠 生活应用
{% for item in action_items.life %}
- [ ] {{ item | replace("\n", " ") }}
{% endfor %}

### 📝 技术总结
{% for item in action_items.tech_summary %}
- [ ] {{ item | replace("\n", " ") }}
{% endfor %}

---

## 📜 时间轴分段笔记

{% for seg in segments %}
> **{{ seg.timestamp }}** — {{ seg.text }}
{% endfor %}

---

## 📝 完整转录文字

<details>
<summary>点击展开原始转录</summary>

{{ transcript }}

</details>
