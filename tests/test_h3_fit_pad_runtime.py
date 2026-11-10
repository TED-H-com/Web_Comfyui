"""Optional CPU test of pinned upstream fit/pad code, with synthetic pixels only.

Set CASTLE_COMFY_NODE_SOURCE to an explicitly reviewed nodes_images.py. Imports
only torch/Pillow/NumPy and executes the three hash-pinned AST definitions below;
never imports ComfyUI startup/model code, loads a model or initializes a GPU.
"""
import ast
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = os.environ.get('CASTLE_COMFY_NODE_SOURCE', '')
PINS = {'ResizeAndPadImage': '4a12a97f7fc84ddb1c96558edd5c0a485af2d541e6599bbd1306e198ea1894a0', 'lanczos': '7bb545076a3f4b02a797d578559789374ad13da448dafc731c5978d2bbb0be21', 'common_upscale': '48e93bc6f26647067f165213e74b839da7944d0e1e945ccac45acbfcc412e04c'}


@unittest.skipUnless(SOURCE, 'Explicit pinned ComfyUI source required for CPU numerical checks')
class FitPadRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        import numpy as np
        from PIL import Image
        torch.set_default_device('cpu')
        torch.set_num_threads(1)
        cls.torch=torch
        image_source=Path(SOURCE)
        utils_source=image_source.parent.parent/'comfy/utils.py'
        namespace={'torch':torch,'np':np,'Image':Image,
                   'IO':SimpleNamespace(ComfyNode=object,NodeOutput=lambda value:value)}
        for path,names in [(utils_source,('lanczos','common_upscale')),
                           (image_source,('ResizeAndPadImage',))]:
            source=path.read_text()
            tree=ast.parse(source)
            definitions=[node for node in tree.body if getattr(node,'name',None) in names]
            assert len(definitions)==len(names)
            for node in definitions:
                digest=hashlib.sha256(ast.get_source_segment(source,node).encode()).hexdigest()
                assert digest==PINS[node.name], 'Upstream implementation changed; review before execution'
            if path==image_source:
                namespace['comfy']=SimpleNamespace(utils=SimpleNamespace(common_upscale=namespace['common_upscale']))
            exec(compile(ast.Module(body=definitions,type_ignores=[]),str(path),'exec'),namespace)
        cls.resize=namespace['ResizeAndPadImage'].execute

    def test_complete_source_geometry_and_corners_across_canvases(self):
        torch=self.torch
        for width,height,target_w,target_h in [
            (1024,1024,864,480),(864,1152,864,480),(1200,600,480,864),
            (320,320,640,640),(300,400,768,1344),(64,4096,864,480),
        ]:
            with self.subTest(source=(width,height),target=(target_w,target_h)):
                image=torch.ones((1,height,width,3),device='cpu')
                radius=max(1,min(width,height)//6)
                yy,xx=torch.meshgrid(torch.arange(height),torch.arange(width),indexing='ij')
                disk=(xx-width//2)**2+(yy-height//2)**2<=radius**2
                image[0,disk]=0
                size=max(1,min(width,height)//10)
                colors=[(1,0,0),(0,1,0),(0,0,1),(1,1,0)]
                for (ys,xs),color in zip([(slice(0,size),slice(0,size)),(slice(0,size),slice(-size,None)),
                                          (slice(-size,None),slice(0,size)),(slice(-size,None),slice(-size,None))],colors):
                    image[0,ys,xs]=torch.tensor(color,dtype=image.dtype)
                original=image.clone()
                output=self.resize(image,target_w,target_h,'black','lanczos')
                self.assertTrue(torch.equal(image,original))
                self.assertEqual(tuple(output.shape),(1,target_h,target_w,3))
                self.assertEqual(output.device.type,'cpu')
                occupied=torch.nonzero(output[0].sum(-1)>0.1)
                y0,x0=occupied.min(0).values.tolist();y1,x1=occupied.max(0).values.tolist()
                scale=min(target_w/width,target_h/height)
                fitted_w,fitted_h=int(width*scale),int(height*scale)
                self.assertEqual((x1-x0+1,y1-y0+1),(fitted_w,fitted_h))
                self.assertEqual((x0,y0),((target_w-fitted_w)//2,(target_h-fitted_h)//2))
                content=output[0,y0:y1+1,x0:x1+1]
                # Four differently colored corners remain; a center crop loses
                # these markers. Skip subpixel marker comparisons for thin art.
                if size*scale>=3:
                    for pixel,color in zip([content[1,1],content[1,-2],content[-2,1],content[-2,-2]],colors):
                        self.assertTrue(torch.allclose(pixel,torch.tensor(color,dtype=pixel.dtype),atol=0.06))
                    # A source circle stays circular within raster rounding.
                    horizontal=(content[fitted_h//2].sum(-1)<0.2).sum().item()
                    vertical=(content[:,fitted_w//2].sum(-1)<0.2).sum().item()
                    self.assertLessEqual(abs(horizontal-vertical),2)

    def test_upstream_subpixel_dimension_limit_is_explicit(self):
        # Document the existing node's zero-dimension limit; never claim every
        # arbitrarily thin input is supported by the fit/pad operation.
        image=self.torch.ones((1,4096,1,3),device='cpu')
        with self.assertRaises((ValueError,RuntimeError)):
            self.resize(image,864,480,'black','lanczos')


if __name__=='__main__':
    unittest.main()
