# gvar 轮廓位移溯源器

拖动可变 TrueType 字体的设计轴时，逐点分辨轮廓位移是**显式编码在 gvar
tuple 里**，还是由**相邻点经 IUP 推断**出来的工具。React 页面叠画基准轮廓
与当前实例，Python 后端直接读取含 `glyf` / `gvar` 的字体完成全部计算——
**不使用系统字体的光栅化渲染结果**。

## 范围（刻意收窄）

- 最多 **2 个设计轴**、**12 个简单字形**、每字形最多 **300 个点**。
- 支持 `fvar` / `avar` / `gvar` / 简单 `glyf`（含离曲线点、多轮廓）。
- **不支持**复合字形、hinting（instruction 被跳过）、`HVAR`、`VVAR`、
  `vmtx`，加载时直接拒绝。
- 生产路径**完全不 import fontTools**：SFNT 目录、
  head/maxp/hhea/hmtx/post/loca/glyf、fvar/avar、gvar 全部用标准库
  （`struct`/`array`/`math`）自行解析。
- fontTools 只出现在 `backend/tests/`：一是构造小测试字体，二是作为
  **独立实例化 oracle** 逐点对照。

## 计算管线（自研，`backend/vfont/`）

1. **fvar 归一化**：用户坐标 clamp 到 `[min,max]`，以 default=0、min=-1、
   max=+1 分段线性映射（轴值按 Fixed 16.16 读取）。
2. **avar 分段映射**：逐轴 piecewise-linear（段外按外段斜率外推）。
3. **F2Dot14 量化**：`otRound(v*16384)/16384`，与真实 shaping 引擎一致。
4. **tuple 权重（support scalar）**：对每个 gvar tuple 的区域帐篷
   `(start,peak,end)` 求标量；峰值为 0 的轴不参与；跨零/非法帐篷权重为 0。
5. **逐 tuple 先 IUP 再加权累加**（与现代 fontTools instancer 的顺序一致）。
6. 最终坐标 `otRound(default + Σ weight·delta)`，其中
   `otRound(v)=floor(v+0.5)`（注意 -0.5 舍到 0）。

### IUP（`iup.py`）按轮廓分别执行，绝不跨轮廓插值

- 每个闭合轮廓独立处理；首尾环绕（wrap-around）的空隙由该轮廓最前/最后的
  已标注点桥接。
- **单个触及点**：其余点全部取该点 delta（常量）。
- **相同原坐标的端点**：参照点原坐标相等时，delta 相等则取该 delta；
  delta 不同则该轴**强制为 0**（规范要求）。
- 区间内线性插值；点落在参照坐标区间**之外时钳制到较近一端，不外推**。
- 整个轮廓没有任何显式点时，全部解析为 `(0,0)`（零触及轮廓）。
- **4 个 phantom points 各自作为单点“轮廓”**，因此永远不混入轮廓插值；
  水平左/右 phantom 的间距用于报告 **advance**（先算浮点再 otRound，
  负 advance 钳到 0）。

每个推断点都带回溯源：参照点编号、`interpolate / clamp_before /
clamp_after / equal_coordinate_constant / equal_coordinate_zero` 规则、
原始 delta 与加权 delta。点级“总来源”取最高优先级
（explicit > inferred > none），而逐 tuple 的 explicit/inferred/none
在详情面板中分别列出。

### gvar 二进制解析（`gvar.py`）

覆盖：shared tuples（索引引用）与 embedded peak tuple、intermediate
region、glyph 级共享点号与每 tuple 私有 packed point runs（byte/word
deltas run、count=0 表示全部点）、zero/int8/int16/**int32** delta runs，
以及**短偏移表需 ×2**（FreeType 行为）和 uint32 长偏移表两种格式。

## 目录

```
backend/
  vfont/            # 生产引擎（纯标准库，零 fontTools 依赖）
    binary.py       # SFNT 目录 + Reader
    normalization.py# fvar 归一化 / avar / F2Dot14 / support scalar / otRound
    glyf.py         # head/maxp/hhea/hmtx/post/loca/简单 glyf + phantom
    gvar.py         # gvar 表与 tuple variation 二进制解析
    iup.py          # 逐轮廓 IUP + 逐点规则溯源
    engine.py       # 装配、求值、加权、累加、舍入、报告
  server.py         # 标准库 HTTP：/api/info /api/instance /api/load + 静态资源
  tests/
    make_fonts.py   # 测试字体生成（fontTools，仅测试）
    oracle.py       # fontTools 独立实例化 + 原始 tuple 解析（仅测试对照）
    test_engine.py  # 33 个测试，逐点核对
frontend/           # React + Vite：滑块、字形选择、SVG 叠画、溯源面板
```

## 运行

```bash
# 后端（默认 8000；首次会用测试生成器造一个内置字体，需 fontTools）
cd backend
python3 -m pip install -r requirements.txt
python3 server.py

# 前端（开发，带 /api 代理）
cd frontend
npm install
npm run dev          # http://localhost:5173

# 或构建后由 Python 直接托管
cd frontend && npm run build   # 产物在 frontend/dist，由 server.py 提供
```

页面可“加载 TTF…”上传自己的可变字体（必须满足上面的范围限制）。

## 测试

```bash
cd backend
python3 -m pytest tests/ -q
```

对照策略：在同一组（轴位置 × 字形）上，用
`fontTools.varLib.instancer.instantiateVariableFont(..., optimize=False)`
独立实例化，按**相同 otRound 规则**逐点核对轮廓坐标、4 个 phantom 与
advance；另外逐 tuple 比对自研解码器与 fontTools 对 gvar 的原始解析
（support、显式点集合、原始 delta），避免最终坐标的误差相互抵消。

覆盖的关键情形：

- **轴折点**：avar 分段顶点及跨段值（-0.5→-0.6、0.5→0.7），F2Dot14
  非整值的量化；
- **默认实例**：全部权重 0、坐标恒等；
- **稀疏 deltas**：只触及单个点（含环绕推断）、int16 越界的 **int32
  run**、负向峰值帐篷；
- **零触及轮廓**：一个轮廓在 tuple 中完全无显式点，且不能借用另一轮廓；
- 相同原坐标端点的零填充、轮廓隔离、phantom-only 的 advance 变化；
- gvar 的短偏移（×2）与手工改写的 **uint32 长偏移**两种编码；
- 空字形 / 单点字形 / 双点轮廓 / 离曲线字形；复合字形被拒绝。

## 界面如何保证“不是渲染截图”

SVG 路径由后端返回的整数坐标在前端用 TrueType 二次贝塞尔规则重建，
虚线画默认实例、实线画当前实例；点按来源着色（橙=显式、蓝=推断、
灰=无贡献、紫=phantom）。全程没有字体字形光栅化或系统字体调用，因此你
看到的每一个像素都可回溯到某条 gvar/IUP 规则与某个点号。
