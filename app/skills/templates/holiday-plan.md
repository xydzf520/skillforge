---
name: holiday-plan
description: "查询节假日信息，辅助排期规划。Use when: 需要规划活动或运营排期时。"
compatibility: "Requires curl and jq"
metadata:
  author: skillforge
  department: EC
  risk-level: R2---

此 Skill 通过查询公开的节假日 API，帮助你获取特定年份和国家的节假日信息，以便进行运营活动排期规划。

操作步骤：

1.  首先，你需要确定要查询的年份和国家代码（例如：CN 代表中国，US 代表美国）。默认查询当前年份和中国的节假日。
2.  使用以下 curl 命令查询节假日数据。你可以通过修改 `year` 和 `country` 参数来调整查询。
    ```bash
    # 查询 2024 年中国的节假日
    curl -s "https://date.nager.at/api/v3/PublicHolidays/2024/CN"
    ```
3.  上述命令会返回一个 JSON 数组。为了更清晰地查看，可以使用 `jq` 工具进行格式化，并提取关键信息（如日期和节日名称）。
    ```bash
    curl -s "https://date.nager.at/api/v3/PublicHolidays/2024/CN" | jq '.[] | {date: .date, name: .localName}'
    ```
4.  如果你没有指定年份和国家，Skill 将尝试查询当前年份和中国的节假日。获取当前年份的 Shell 命令是 `date +%Y`。
    ```bash
    CURRENT_YEAR=$(date +%Y) && curl -s "https://date.nager.at/api/v3/PublicHolidays/${CURRENT_YEAR}/CN" | jq '.[] | {date: .date, name: .localName}'
    ```
5.  将查询到的节假日列表与你计划的运营活动时间进行比对，避开法定假日或利用假日流量高峰，完成初步的排期规划。

注意：此 Skill 依赖于公开的 [Nager.Date API](https://date.nager.at)，请确保网络可访问此服务。`jq` 是一个命令行 JSON 处理器，如果未安装，你可能需要先安装它（例如，在 macOS 上使用 `brew install jq`，在 Ubuntu 上使用 `sudo apt-get install jq`）。