Doubao Seedream 5.0 pro（以下简称 Seedream 5.0 pro）与 Doubao Seedream 5.0 flash（以下简称 Seedream 5.0 flash）均提供交互编辑能力，支持在提示词中输入位置坐标来精准编辑图片。用户可以在参考图上通过坐标点或标注建立位置对应关系，模型将按标记位置进行局部编辑，实现物品替换、元素定位、局部重绘等精细化操作。

本文介绍如何基于 Seedream 5.0 pro / flash 实现点选和框选式交互编辑：用户上传参考图后，通过点选或框选指定编辑位置，前端将操作位置转换为**归一化坐标**（范围 **0~999**，左上角为 0,0，右下角为 999,999）用 `<point>` 或 `<bbox>` 坐标标记，并与自然语言 Prompt 一起提交给模型。模型会结合参考图、坐标位置和文本指令生成编辑后的图片。

| 输入图片及框选操作                                                                                                                                                                                                                                                                                       | 效果预览                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [video](https://arkdocs.tos-cn-beijing.volces.com/videos/image-generation/edite-model.mov)<br><br><br> <br><br>> 将用户框选的目标区域转换为空间坐标，组装成模型可以理解的 Prompt。<br><br>> <br><br>> Prompt：`将图 1 <bbox> 179 283 796 986</bbox> 的主体放到图 2 <bbox> 118 331 933 871</bbox> 位置。` | ![图片](https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/edit-image.png)<br><br> <br><br>> 根据组装后的 Prompt、参考图生成编辑后的图片。 |

# 关键操作：将目标区域转换为归一化坐标

模型需要的关键信息：**待编辑图片**和 **prompt**（包含归一化坐标 + 编辑指令），其中归一化坐标是将图片上点选或框选的区域**归一化到 1000 \* 1000 的比例坐标，取值范围[0,999]** 。图片宽高等分为 1000 份后，图片左上角坐标为 `0,0`，右下角为 `999,999`。

**prompt 中可以使用的坐标方式**：

- **点选坐标**：`<point>x y</point>`（指定一个点，由模型判断影响范围）

- **框选坐标**：`<bbox>x1 y1 x2 y2</bbox>`（指定左上角和右下角坐标，精确控制编辑区域大小）

**归一化坐标处理方式**：

1. 获取定位信息：用户完成点选或框选后，需要先拿到定位点/框在图片展示区域内的相对坐标（相对于图片左上角）。
   - 点选：`x_px, y_px`（点击位置）

   - 框选：`x1_px, y1_px, x2_px, y2_px`（框的左上角与右下角）

2. 转换为 0\-999 的归一化坐标：将获取到的定位坐标按图片展示宽高转换为 0\-999 的整数坐标。
   - 点选：
     - `x = round(x_px / width * 1000)`

     - `y = round(y_px / height * 1000)`

     > **注意**
     - > `x`、`y`：归一化后的坐标。

     - > `x_px`、`y_px`：点选位置相对于图片左上角的坐标。

     - > `width`、`height`：图片在画布中的展示宽、高。

   - 框选：

     对 `x1_px, y1_px, x2_px, y2_px` 分别按上述规则换算，得到 `x1 y1 x2 y2`。

# 支持场景

> **说明**

> 在多主体场景下明确指定编辑对象的方法，可参见 [使用说明](https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-5-0-pro-editing-guide#usage)。

| 场景                 | 交互模式   | 组合后的 Prompt                                                                                                                  |
| -------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 编辑指定点附近的对象 | 点选       | `把图 1 <point>520 460</point> 位置换成皇冠`                                                                                     |
| 编辑指定区域的对象   | 框选       | `把图 1 <bbox>120 180 640 760</bbox> 区域替换成花园`                                                                             |
| 跨图片编辑           | 点选、框选 | `将图 1 <bbox>179 283 796 986</bbox> 的主体放到图 2 <bbox>118 331 933 871</bbox> 位置，将图 2 <point>50 50</point> 位置换成皇冠` |

# 整体流程

## 代码示例

您可以下载代码示例，体验交互编辑效果。下载并解压后，请确保已安装 Python 3.9 或以上版本，在项目目录执行 `python3 -m pip install -r requirements.txt` 安装依赖，设置环境变量 `ARK_API_KEY`，再执行 `bash run.sh` 启动示例。

[touch_edit_demo_arkruntime-20260909.zip](https://arkdocs.tos-cn-beijing.volces.com/files/image-generation/touch_edit_demo_arkruntime-20260909.zip)

## 流程图

基于上述代码示例实现交互编辑的完整流程如下。

![图片](https://arkdocs.tos-cn-beijing.volces.com/flowcharts/image-generation/new-202607111020.svg)

# 实现步骤

> **注意**

> 本文仅提供展示关键执行逻辑的核心代码，无法仅凭以下内容实现完整流程。完整实现请参见 [代码示例](https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-5-0-pro-editing-guide#code-example)。

## 0. 上传图片并放入画布

这一步主要用于获取图片的相关信息。本文示例是将用户上传的图片转换为画布中的可操作对象。除了保存图片文件本身，还需要记录图片在画布中的位置、展示尺寸、原始尺寸和图号，为后续点选、框选和模型调用提供基础数据。

核心代码如下：

核心代码

```JavaScript
function addImageFromFile(file, dataUrl) {
  const probe = new Image();
  probe.onload = () => {
    const maxSide = 360;
    const ratio = Math.min(1, maxSide / Math.max(probe.naturalWidth, probe.naturalHeight));
    const id = `img-${state.nextId}`;
    const image = {
      id,
      label: `图${state.nextId}`,
      name: file.name,
      dataUrl,
      element: probe,
      naturalWidth: probe.naturalWidth,
      naturalHeight: probe.naturalHeight,
      x: 80 + (state.nextId - 1) * 40,
      y: 80 + (state.nextId - 1) * 40,
      width: Math.round(probe.naturalWidth * ratio),
      height: Math.round(probe.naturalHeight * ratio),
    };
    state.nextId += 1;
    state.images.push(image);
    selectImage(id);
    setStatus(`已上传 ${image.label}，可拖拽或点选/框选。`);
  };
  probe.src = dataUrl;
}
```

## 1. 将目标区域转换为归一化坐标

这一步将用户在画布上的点选或框选操作，转换为 Seedream 5.0 pro Prompt 中可使用的坐标定位标记。

### 实现原理

在图片上点选或框选的区域，需要转换为归一化坐标（范围 0~999，左上角为 0,0，右下角为 999,999），将用户的空间意图显式写入 Prompt，例如：

```text
把 图1<point>520 460</point> 位置换成皇冠
把 图1<bbox>120 180 640 760</bbox> 区域替换成花园
```

- **坐标说明**

  | 坐标类型        | 含义                                                                         | 用途                                   |
  | --------------- | ---------------------------------------------------------------------------- | -------------------------------------- |
  | Client 坐标     | 鼠标相对浏览器窗口左上角的位置，即 `event.clientX / event.clientY`。         | 浏览器事件的原始输入。                 |
  | World 坐标      | 画布内部的逻辑坐标系。图片的 `x / y / width / height` 都保存在这个坐标系中。 | 保证画布缩放、平移后仍能正确命中图片。 |
  | Normalized 坐标 | 相对单张图片归一化到 0\-999 的坐标。                                         | 最终写入 `<point>` 或 `<bbox>`。       |

- **坐标转换**
  1.  将鼠标的 Client 坐标转换为 World 坐标，消除画布位置、平移和缩放的影响。

  2.  将 World 坐标转换为图片内 0\-999 的归一化坐标。具体原理参见 [关键操作：将目标区域转换为归一化坐标](https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-5-0-pro-editing-guide#key-steps)。
      - 点选模式：生成 `<point> x y</point>`；

      - 框选模式：生成 `<bbox> x1 y1 x2 y2</bbox>`。

### 核心代码

- 坐标转换

  ```JavaScript
  // Client 坐标转换为 World 坐标
  function clientToWorld(clientX, clientY) {
    const rect = viewport.getBoundingClientRect();
    return {
      x: (clientX - rect.left - state.view.x) / state.view.scale,
      y: (clientY - rect.top - state.view.y) / state.view.scale,
    };
  }

  // World 坐标转换为 0-999 归一化
  function normalizedPoint(worldPoint, image) {
    return {
      x: clamp1000(((worldPoint.x - image.x) / image.width) * 1000),
      y: clamp1000(((worldPoint.y - image.y) / image.height) * 1000),
    };
  }
  ```

- 根据点选 / 框选生成 `<point>` 或 `<bbox>` 坐标标记

  ```JavaScript
  if (state.mode === "point" && image) {
      const p = normalizedPoint(worldPoint, image);
      const ann = buildAnnotation("point", image, { x: p.x, y: p.y });
      ann.token = `${image.label}<point>${p.x} ${p.y}</point>`;
      state.annotations.push(ann);
      appendAnnotationChip(ann, image);
      renderImages();
      setStatus(`已添加 ${ann.token}`);
      return;
  }

  if (state.box) {
      const image = state.images.find((item) => item.id === state.box.imageId);
      const dx = Math.abs(state.box.current.x - state.box.start.x);
      const dy = Math.abs(state.box.current.y - state.box.start.y);
      state.box.selection.remove();
      if (image && dx > 4 && dy > 4) {
        const b = normalizedBox(state.box.start, state.box.current, image);
        const ann = buildAnnotation("bbox", image, b);
        ann.token = `${image.label}<bbox>${b.x1} ${b.y1} ${b.x2} ${b.y2}</bbox>`;
        state.annotations.push(ann);
        appendAnnotationChip(ann, image);
        setStatus(`已添加 ${ann.token}`);
      }
      state.box = null;
      viewport.releasePointerCapture(event.pointerId);
      renderImages();
  }
  ```

## 2. 组装 Prompt

这一步将用户输入的自然语言和点选 / 框选生成的空间坐标合并，形成 Seedream 5.0 pro 可以理解的最终 Prompt。

### Prompt 写法

| 场景                 | 参考写法                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------ |
| 编辑指定点附近的对象 | `把图 1 <point> 520 460</point> 位置换成皇冠`                                              |
| 编辑指定区域的对象   | `把图 1 <bbox> 120 180 640 760</bbox> 区域替换成花园`                                      |
| 跨图片编辑           | `将图 1 <bbox> 179 283 796 986</bbox> 的主体放到图 2 <bbox> 118 331 933 871</bbox> 位置。` |

### 核心代码

核心代码

```JavaScript
// 从编辑框内容生成最终 Prompt，并整理本次需要提交的图片列表
function buildModelInputFromPrompt() {
  const assignedImages = new Map();
  const inputImages = [];

  // 将画布图片映射为模型输入的图号，并收集需要提交的图片
  function assignImage(image) {
    if (!image) return "";
    if (!assignedImages.has(image.id)) {
      const inputLabel = `图${inputImages.length + 1}`;
      assignedImages.set(image.id, inputLabel);
      inputImages.push({ ...image, inputLabel });
    }
    return assignedImages.get(image.id);
  }

  // 将编辑框中的图号（图1/图2）重映射为实际要提交的输入图号
  function remapImageLabels(text) {
    return text.replace(/图\\d+/g, (label) => {
      const image = state.images.find((item) => item.label === label);
      return image ? assignImage(image) : label;
    });
  }

  // 遍历编辑框 DOM，将文本与坐标标注拼接为 Prompt 文本
  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return remapImageLabels(node.textContent || "");
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }
    if (node.classList?.contains("annotation-inline")) {
      const ann = state.annotations.find((item) => item.id === Number(node.dataset.annotationId));
      const image = ann ? state.images.find((item) => item.id === ann.imageId) : null;
      const inputLabel = assignImage(image);
      if (!ann || !inputLabel) return "";
      return ` ${annotationTokenForLabel(ann, inputLabel)} `;
    }
    if (node.tagName === "BR") {
      return "\\n";
    }
    return [...node.childNodes].map(walk).join("");
  }

  const prompt = walk(promptInput)
    .replace(/\\u00a0/g, " ")
    .replace(/[ \\t]{2,}/g, " ")
    .replace(/\\n{3,}/g, "\\n\\n")
    .trim();

  return { prompt, images: inputImages };
}
```

## 3. 生成图片

这一步根据组装好的 Prompt 和输入图片调用图片生成 API，并将模型生成结果返回给前端展示。

### 核心代码

- 前端提交图片生成请求

  ```JavaScript
  generateBtn.addEventListener("click", async () => {
    const modelInput = buildModelInputFromPrompt();
    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: modelInput.prompt,
        images: modelInput.images.map((image) => ({
          inputLabel: image.inputLabel,
          name: image.name,
          dataUrl: image.dataUrl,
          naturalWidth: image.naturalWidth,
          naturalHeight: image.naturalHeight,
        })),
      }),
    });

    const data = await resp.json();
    renderGeneratedImage(data.url);
  });
  ```

- 后端调用图片生成 API

  ```Python
  ARK_MODEL = "doubao-seedream-5-0-pro-260628"
  ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

  try:
      client = Ark(
          api_key=os.getenv("ARK_API_KEY"),
          base_url=ARK_BASE_URL,
      )
      resp = client.images.generate(
          model=ARK_MODEL,
          prompt=prompt,
          image=image_arg,
          size="2K",
          output_format="png",
          response_format="url",
          watermark=True,
      )
  except Exception as exc:
      print(f"[generate] error: {exc!r}")
      return self._send_json(500, {"error": str(exc)})

  try:
      url = resp.data[0].url
  except (AttributeError, IndexError) as exc:
      return self._send_json(502, {"error": f"unexpected ark response: {exc}"})

  return self._send_json(
      200,
      {
          "model": ARK_MODEL,
          "prompt": prompt,
          "url": url,
      },
  )
  ```

### 效果预览

| 输入图片                                                                                                                                                                                                             | 效果预览                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| ![图片](https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/image-20260711-161109-398.png)<br><br>> Prompt：`将图 1 <bbox> 179 283 796 986</bbox> 的主体放到图 2 <bbox> 118 331 933 871</bbox> 位置。` | ![图片](https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/edit-image.png) |

# 使用说明

在多主体场景下，可以通过以下写法更明确地指定编辑对象。

- 框选区域内有多个主体时，明确操作对象。

  当 `<bbox>` 覆盖的区域内存在多个主体/元素时，建议在 Prompt 中补充一句说明要编辑的目标对象（例如“左侧的人”“戴帽子的猫”“前景的花”），帮助模型稳定选择正确的编辑对象。

  示例：`把图 1 <bbox>120 180 640 760</bbox> 区域内的左侧人物换成机器人`

- 标注需要保持不变的对象。

  如果希望某些物体不被修改，可以将其一并框选出来，并在 Prompt 中明确“保持不变/不要修改”。

  示例：`把图 1 <bbox>120 180 640 760</bbox> 区域替换成花园，图 1 <bbox>700 120 920 360</bbox> 区域保持不变`

# 相关文档

- [Doubao Seedream 5.0 pro / flash 教程](https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-5-0-pro)

- [图片生成 API 文档](https://ark.volcengine.com/region:cn-beijing/docs/ark/image-generation-api)
