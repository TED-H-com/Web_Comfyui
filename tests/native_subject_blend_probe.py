"""CPU-only worker for the opt-in real ComfyUI node regression test."""
from pathlib import Path
import io
import json
import sys


def main():
    comfy_home, graph_path = sys.argv[1:]
    graphs = json.loads(Path(graph_path).read_text())
    sys.path.insert(0, comfy_home)
    sys.argv = ['subject-blend-probe', '--cpu']
    import comfy.options
    comfy.options.enable_args_parsing()
    # Some optional Triton versions initialize a GPU driver even in CPU mode.
    sys.modules['triton'] = None
    import torch
    import numpy as np
    from PIL import Image
    from scipy.ndimage import binary_erosion, binary_dilation
    from comfy_extras.nodes_mask import (
        ThresholdMask, MaskToImage, ImageToMask, GrowMask, ImageCompositeMasked,
    )
    from comfy_extras.nodes_post_processing import Blur
    torch.set_num_threads(2)
    classes = {cls.__name__: cls for cls in (
        ThresholdMask, MaskToImage, ImageToMask, GrowMask, ImageCompositeMasked,
    )}
    classes['ImageBlur'] = Blur

    def png(tensor):
        raw = (tensor * 255).clamp(0, 255).numpy().astype(np.uint8)[0]
        stream = io.BytesIO()
        Image.fromarray(raw).save(stream, format='PNG')
        stream.seek(0)
        return np.asarray(Image.open(stream)).copy()

    source = torch.arange(256, dtype=torch.float32).view(1, 1, 256, 1).expand(1, 128, 256, 3) / 255
    background = 1 - source
    source_png, background_png = png(source), png(background)
    assert np.array_equal(source_png[0, :, 0], np.arange(256, dtype=np.uint8))
    mask = torch.zeros((1, 128, 256))
    mask[:, 24:104, 32:224] = 1
    masks = {'rectangle': mask}
    mask = mask.clone(); mask[:, 52:76, 100:156] = 0
    masks['hole'] = mask
    mask = torch.zeros_like(mask); mask[:, :80, :150] = 1
    masks['canvas_edge'] = mask
    masks['empty'] = torch.zeros_like(mask)
    masks['full'] = torch.ones_like(mask)
    mask = torch.zeros_like(mask); mask[:, 60:64, 20:230] = 1
    masks['thin'] = mask
    cases = 0
    for shape, mask in masks.items():
        for radius_text, graph in graphs.items():
            radius = int(radius_text)
            # Replay the emitted graph after SAM3 detection/background resize.
            # Those expensive upstream nodes are covered by separate full runs.
            results = {'1': (source,), '3': (background,), '6': (mask,)}

            def output(node_id):
                if node_id not in results:
                    node = graph[node_id]
                    inputs = {key: output(value[0])[value[1]] if isinstance(value, list) else value
                              for key, value in node['inputs'].items()}
                    results[node_id] = classes[node['class_type']].execute(**inputs)
                return results[node_id]

            link = graph['9']['inputs']['images']
            saved = png(output(link[0])[link[1]])
            arr = mask[0].numpy().astype(bool)
            inside = arr if radius == 0 else binary_erosion(arr, np.ones((3, 3)), iterations=radius, border_value=1)
            outside = ~arr if radius == 0 else ~binary_dilation(arr, np.ones((3, 3)), iterations=radius, border_value=0)
            assert np.array_equal(saved[inside], source_png[inside]), (shape, radius, 'interior drift')
            assert np.array_equal(saved[outside], background_png[outside]), (shape, radius, 'background drift')
            if radius and shape == 'rectangle':
                binary = np.where(arr[..., None], source_png, background_png)
                assert np.any(saved != binary), (shape, radius, 'no feathering')
            cases += 1
    assert not torch.cuda.is_initialized()
    print(json.dumps({'passed_cases': cases, 'cuda_initialized': False}))


if __name__ == '__main__':
    main()
