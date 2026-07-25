# worlds/ — 编译产出的 WorldPackage

这个目录存放**小说编译器**（`compiler/`）从 TXT 自动编译出的 WorldPackage JSON。

文件本身是编译产物，不纳入版本控制（见 `.gitignore`）。需要时重新编译即可。

## 如何编译

```bash
# 编译《第一狂妃》第 1 章
.venv/Scripts/python.exe -m compiler.cli "novels/第一狂妃：废柴三小姐.txt" \
    -c 1 --package-id huarong_lane_compiled \
    -o worlds/huarong_lane_compiled.json

# 编译前 2 章
.venv/Scripts/python.exe -m compiler.cli "novels/第一狂妃：废柴三小姐.txt" -c 1 2 \
    -o worlds/huarong_lane_ch1-2.json
```

## 与手工版的关系

`examples/huarong_lane/` 是**手工建模**的基准世界包（取自原著第 1-2 章）。
编译器产出的包与之**同构**（同样的 Character/Item/Relation/WorldRule 结构），
用来验证"TXT → WorldPackage"自动化是否忠实。

详见 `docs/实现进度.md` 第 1.6 节。
