"""Reviewed real GPU examples. Run evidence lives in webui/docs/examples/provenance.json."""
RECORDED_DEMOS = {'text2img': {'title': '生成原創成人應援角色',
              'title_en': 'Generate an original adult cheer performer',
              'prompt': 'Photorealistic sports editorial full-body photograph of a fictional 26-year-old '
                        'Taiwanese woman baseball cheer performer, warm friendly smile, dark hair in a '
                        'ponytail, natural makeup, original teal and white short-sleeve baseball jersey '
                        'covering torso, white athletic shorts to mid thigh, white sneakers, standing '
                        'relaxed with both hands visible at sides, all feet visible with space around the '
                        'body, empty baseball stadium in afternoon sunshine, beautiful natural adult face, '
                        'professional athletic posture, realistic anatomy, no logos or text',
              'prompt_en': 'Photorealistic sports editorial full-body photograph of a fictional 26-year-old '
                           'Taiwanese woman baseball cheer performer, warm friendly smile, dark hair in a '
                           'ponytail, natural makeup, original teal and white short-sleeve baseball jersey '
                           'covering torso, white athletic shorts to mid thigh, white sneakers, standing '
                           'relaxed with both hands visible at sides, all feet visible with space around the '
                           'body, empty baseball stadium in afternoon sunshine, beautiful natural adult '
                           'face, professional athletic posture, realistic anatomy, no logos or text',
              'prefill': {'positive': 'Photorealistic sports editorial full-body photograph of a fictional '
                                      '26-year-old Taiwanese woman baseball cheer performer, warm friendly '
                                      'smile, dark hair in a ponytail, natural makeup, original teal and '
                                      'white short-sleeve baseball jersey covering torso, white athletic '
                                      'shorts to mid thigh, white sneakers, standing relaxed with both hands '
                                      'visible at sides, all feet visible with space around the body, empty '
                                      'baseball stadium in afternoon sunshine, beautiful natural adult face, '
                                      'professional athletic posture, realistic anatomy, no logos or text',
                          'negative': 'watermark, logo, text, deformed hands, extra fingers, extra limbs, '
                                      'child, blurry',
                          'width': 832,
                          'height': 1216,
                          'steps': 9,
                          'cfg': 1,
                          'seed': 2026102302,
                          'sampler': 'res_multistep',
                          'scheduler': 'simple',
                          'aspect': '832x1216'},
              'prefill_en': {'positive': 'Photorealistic sports editorial full-body photograph of a '
                                         'fictional 26-year-old Taiwanese woman baseball cheer performer, '
                                         'warm friendly smile, dark hair in a ponytail, natural makeup, '
                                         'original teal and white short-sleeve baseball jersey covering '
                                         'torso, white athletic shorts to mid thigh, white sneakers, '
                                         'standing relaxed with both hands visible at sides, all feet '
                                         'visible with space around the body, empty baseball stadium in '
                                         'afternoon sunshine, beautiful natural adult face, professional '
                                         'athletic posture, realistic anatomy, no logos or text',
                             'negative': 'watermark, logo, text, deformed hands, extra fingers, extra limbs, '
                                         'child, blurry',
                             'width': 832,
                             'height': 1216,
                             'steps': 9,
                             'cfg': 1,
                             'seed': 2026102302,
                             'sampler': 'res_multistep',
                             'scheduler': 'simple',
                             'aspect': '832x1216'},
              'inputs': [],
              'evidence': {'kind': 'local-comfyui',
                           'run_id': 'cheer-text2img-v1',
                           'workflow': 'zimage_prompt',
                           'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                           'models': ['ae.safetensors',
                                      'qwen_3_4b.safetensors',
                                      'z_image_turbo_bf16.safetensors'],
                           'prompt': 'Photorealistic sports editorial full-body photograph of a fictional '
                                     '26-year-old Taiwanese woman baseball cheer performer, warm friendly '
                                     'smile, dark hair in a ponytail, natural makeup, original teal and '
                                     'white short-sleeve baseball jersey covering torso, white athletic '
                                     'shorts to mid thigh, white sneakers, standing relaxed with both hands '
                                     'visible at sides, all feet visible with space around the body, empty '
                                     'baseball stadium in afternoon sunshine, beautiful natural adult face, '
                                     'professional athletic posture, realistic anatomy, no logos or text',
                           'negative_prompt': 'watermark, logo, text, deformed hands, extra fingers, extra '
                                              'limbs, child, blurry',
                           'duration_seconds': 8.188,
                           'executed_at': '2026-10-22T20:00:16.436000+00:00',
                           'seed': 2026102302,
                           'steps': 9,
                           'cfg': 1,
                           'sampler': 'res_multistep',
                           'scheduler': 'simple',
                           'width': 832,
                           'height': 1216},
              'output': {'kind': 'image',
                         'images': [{'src': './assets/examples/cheer-text2img.webp',
                                     'alt': '生成原創成人應援角色；此為實際 GPU 輸出。',
                                     'alt_en': 'Generate an original adult cheer performer; actual GPU '
                                               'output.'}],
                         'run_id': 'cheer-text2img-v1'},
              'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。',
              'note_en': 'Recorded on a local RTX 5090, not generated from your input on this website. The '
                         'performer is an original fictional adult.'},
 'img2img': {'title': '球衣換色：青綠改紅、白短褲改深藍',
             'title_en': 'Uniform recolor: teal to red, white shorts to navy',
             'prompt': 'Change the teal parts of the baseball jersey to bright coral red, and change the '
                       'white shorts to navy blue. Keep the same adult woman, her face, pose, white shoes, '
                       'background and lighting unchanged. No logos or text.',
             'prompt_en': 'Change the teal parts of the baseball jersey to bright coral red, and change the '
                          'white shorts to navy blue. Keep the same adult woman, her face, pose, white '
                          'shoes, background and lighting unchanged. No logos or text.',
             'prefill': {'instruction': 'Change the teal parts of the baseball jersey to bright coral red, '
                                        'and change the white shorts to navy blue. Keep the same adult '
                                        'woman, her face, pose, white shoes, background and lighting '
                                        'unchanged. No logos or text.',
                         'steps': 22,
                         'guidance': 2.5,
                         'denoise': 1,
                         'seed': 2026102303,
                         'sampler': 'euler',
                         'scheduler': 'simple'},
             'prefill_en': {'instruction': 'Change the teal parts of the baseball jersey to bright coral '
                                           'red, and change the white shorts to navy blue. Keep the same '
                                           'adult woman, her face, pose, white shoes, background and '
                                           'lighting unchanged. No logos or text.',
                            'steps': 22,
                            'guidance': 2.5,
                            'denoise': 1,
                            'seed': 2026102303,
                            'sampler': 'euler',
                            'scheduler': 'simple'},
             'inputs': [{'src': './assets/examples/cheer-reference.webp',
                         'alt': '原始角色參考圖',
                         'alt_en': 'Original performer reference',
                         'label': '原始角色參考圖',
                         'label_en': 'Original performer reference',
                         'slot': 'image',
                         'kind': 'image'}],
             'evidence': {'kind': 'local-comfyui',
                          'run_id': 'cheer-kontext-v1',
                          'workflow': 'flux_kontext_prompt',
                          'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                          'models': ['ae.safetensors',
                                     'clip_l.safetensors',
                                     'flux1-dev-kontext_fp8_scaled.safetensors',
                                     't5xxl_fp8_e4m3fn_scaled.safetensors'],
                          'prompt': 'Change the teal parts of the baseball jersey to bright coral red, and '
                                    'change the white shorts to navy blue. Keep the same adult woman, her '
                                    'face, pose, white shoes, background and lighting unchanged. No logos or '
                                    'text.',
                          'negative_prompt': '',
                          'duration_seconds': 21.977,
                          'executed_at': '2026-10-22T20:01:05.959000+00:00',
                          'seed': 2026102303,
                          'steps': 22,
                          'guidance': 2.5,
                          'sampler': 'euler',
                          'scheduler': 'simple',
                          'width': 832,
                          'height': 1248},
             'output': {'kind': 'image',
                        'images': [{'src': './assets/examples/cheer-kontext.webp',
                                    'alt': '球衣換色：青綠改紅、白短褲改深藍；此為實際 GPU 輸出。',
                                    'alt_en': 'Uniform recolor: teal to red, white shorts to navy; actual '
                                              'GPU output.'}],
                        'run_id': 'cheer-kontext-v1', 'comparison': True},
             'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。',
             'note_en': 'Recorded on a local RTX 5090, not generated from your input on this website. The '
                        'performer is an original fictional adult.'},
 'image-blend': {'title': '人物與夜間球場參考融合',
                 'title_en': 'Blend performer and evening stadium references',
                 'prompt': 'Candid realistic photograph of a smiling adult Taiwanese woman with long dark '
                           'hair in a ponytail, plain unmarked teal and white baseball jersey, half body '
                           'portrait, standing in an empty baseball stadium during blue hour, soft stadium '
                           'floodlights illuminate her face, detailed natural skin texture, realistic eyes, '
                           '85mm portrait lens, shallow depth of field, contemporary sports editorial '
                           'photograph, unbranded clothing',
                 'prompt_en': 'Candid realistic photograph of a smiling adult Taiwanese woman with long dark '
                              'hair in a ponytail, plain unmarked teal and white baseball jersey, half body '
                              'portrait, standing in an empty baseball stadium during blue hour, soft '
                              'stadium floodlights illuminate her face, detailed natural skin texture, '
                              'realistic eyes, 85mm portrait lens, shallow depth of field, contemporary '
                              'sports editorial photograph, unbranded clothing',
                 'prefill': {'positive': 'Candid realistic photograph of a smiling adult Taiwanese woman '
                                         'with long dark hair in a ponytail, plain unmarked teal and white '
                                         'baseball jersey, half body portrait, standing in an empty baseball '
                                         'stadium during blue hour, soft stadium floodlights illuminate her '
                                         'face, detailed natural skin texture, realistic eyes, 85mm portrait '
                                         'lens, shallow depth of field, contemporary sports editorial '
                                         'photograph, unbranded clothing',
                             'negative': 'illustration, 3d, render, cartoon, doll, painting, plastic, overly '
                                         'smooth skin, overexposed, oversaturated, deformed, watermark, '
                                         'text, logo, symbols, writing, emblem, badge, duplicate person, low '
                                         'quality',
                             'width': 896,
                             'height': 1152,
                             'steps': 36,
                             'cfg': 5.0,
                             'weight': 0.35,
                             'batchSize': 1,
                             'seed': 2026102314,
                             'sampler': 'dpmpp_2m',
                             'scheduler': 'karras',
                             'aspect': '896x1152'},
                 'prefill_en': {'positive': 'Candid realistic photograph of a smiling adult Taiwanese woman '
                                            'with long dark hair in a ponytail, plain unmarked teal and '
                                            'white baseball jersey, half body portrait, standing in an empty '
                                            'baseball stadium during blue hour, soft stadium floodlights '
                                            'illuminate her face, detailed natural skin texture, realistic '
                                            'eyes, 85mm portrait lens, shallow depth of field, contemporary '
                                            'sports editorial photograph, unbranded clothing',
                                'negative': 'illustration, 3d, render, cartoon, doll, painting, plastic, '
                                            'overly smooth skin, overexposed, oversaturated, deformed, '
                                            'watermark, text, logo, symbols, writing, emblem, badge, '
                                            'duplicate person, low quality',
                                'width': 896,
                                'height': 1152,
                                'steps': 36,
                                'cfg': 5.0,
                                'weight': 0.35,
                                'batchSize': 1,
                                'seed': 2026102314,
                                'sampler': 'dpmpp_2m',
                                'scheduler': 'karras',
                                'aspect': '896x1152'},
                 'inputs': [{'src': './assets/examples/cheer-reference.webp',
                             'alt': '人物參考圖',
                             'alt_en': 'Performer reference',
                             'label': '人物參考圖',
                             'label_en': 'Performer reference',
                             'slot': 'image1',
                             'kind': 'image'},
                            {'src': './assets/examples/stadium-reference.webp',
                             'alt': '球場氛圍參考圖',
                             'alt_en': 'Stadium reference',
                             'label': '球場氛圍參考圖',
                             'label_en': 'Stadium reference',
                             'slot': 'image2',
                             'kind': 'image'}],
                 'evidence': {'kind': 'local-comfyui',
                              'run_id': 'cheer-blend-v2',
                              'workflow': 'ip_blend_prompt',
                              'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                              'models': ['CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors',
                                         'ip-adapter-plus_sdxl_vit-h.safetensors',
                                         'sd_xl_base_1.0.safetensors'],
                              'prompt': 'Candid realistic photograph of a smiling adult Taiwanese woman with '
                                        'long dark hair in a ponytail, plain unmarked teal and white '
                                        'baseball jersey, half body portrait, standing in an empty baseball '
                                        'stadium during blue hour, soft stadium floodlights illuminate her '
                                        'face, detailed natural skin texture, realistic eyes, 85mm portrait '
                                        'lens, shallow depth of field, contemporary sports editorial '
                                        'photograph, unbranded clothing',
                              'negative_prompt': 'illustration, 3d, render, cartoon, doll, painting, '
                                                 'plastic, overly smooth skin, overexposed, oversaturated, '
                                                 'deformed, watermark, text, logo, symbols, writing, emblem, '
                                                 'badge, duplicate person, low quality',
                              'duration_seconds': 7.538,
                              'executed_at': '2026-10-22T20:05:36.327000+00:00',
                              'seed': 2026102314,
                              'steps': 36,
                              'cfg': 5.0,
                              'weight': 0.35,
                              'sampler': 'dpmpp_2m',
                              'scheduler': 'karras',
                              'width': 896,
                              'height': 1152},
                 'output': {'kind': 'image',
                            'images': [{'src': './assets/examples/cheer-blend.webp',
                                        'alt': '人物與夜間球場參考融合；此為實際 GPU 輸出。',
                                        'alt_en': 'Blend performer and evening stadium references; actual '
                                                  'GPU output.'}],
                            'run_id': 'cheer-blend-v2'},
                 'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。 IPAdapter '
                         '會重新創作人物外觀與服裝，不是精準保留身分或像素合成。',
                 'note_en': 'Recorded on a local RTX 5090, not generated from your input on this website. '
                            'The performer is an original fictional adult. IPAdapter reinterprets appearance '
                            'and clothing; it is not an exact identity-preserving composite.'},
 'character-angles': {'title': '臉部參考：夜間球場肖像候選',
                      'title_en': 'Face reference: evening stadium portrait candidates',
                      'prompt': 'RAW candid close-up photograph of a 26-year-old adult Taiwanese woman, '
                                'natural expression, realistic brown eyes, realistic human facial '
                                'proportions, realistic adult face, fine natural skin pores, long dark hair, '
                                'wearing a plain dark navy sports jacket, head and shoulders, evening '
                                'baseball stadium with blurred warm lights, soft natural lighting, Nikon '
                                'full frame camera, 85mm f1.8, detailed photograph',
                      'prompt_en': 'RAW candid close-up photograph of a 26-year-old adult Taiwanese woman, '
                                   'natural expression, realistic brown eyes, realistic human facial '
                                   'proportions, realistic adult face, fine natural skin pores, long dark '
                                   'hair, wearing a plain dark navy sports jacket, head and shoulders, '
                                   'evening baseball stadium with blurred warm lights, soft natural '
                                   'lighting, Nikon full frame camera, 85mm f1.8, detailed photograph',
                      'prefill': {'positive': 'RAW candid close-up photograph of a 26-year-old adult '
                                              'Taiwanese woman, natural expression, realistic brown eyes, '
                                              'realistic human facial proportions, realistic adult face, '
                                              'fine natural skin pores, long dark hair, wearing a plain dark '
                                              'navy sports jacket, head and shoulders, evening baseball '
                                              'stadium with blurred warm lights, soft natural lighting, '
                                              'Nikon full frame camera, 85mm f1.8, detailed photograph',
                                  'negative': 'anime, manga, cartoon, illustration, painting, 3d render, '
                                              'doll, plastic, airbrushed skin, overexposed, oversized eyes, '
                                              'huge eyes, giant eyes, juvenile face, child, bad anatomy, '
                                              'distorted fingers, text, logo, watermark, cropped head',
                                  'width': 896,
                                  'height': 1152,
                                  'steps': 36,
                                  'cfg': 5.5,
                                  'weight': 0.6,
                                  'batchSize': 2,
                                  'seed': 2026102325,
                                  'sampler': 'dpmpp_2m',
                                  'scheduler': 'karras',
                                  'aspect': '896x1152'},
                      'prefill_en': {'positive': 'RAW candid close-up photograph of a 26-year-old adult '
                                                 'Taiwanese woman, natural expression, realistic brown eyes, '
                                                 'realistic human facial proportions, realistic adult face, '
                                                 'fine natural skin pores, long dark hair, wearing a plain '
                                                 'dark navy sports jacket, head and shoulders, evening '
                                                 'baseball stadium with blurred warm lights, soft natural '
                                                 'lighting, Nikon full frame camera, 85mm f1.8, detailed '
                                                 'photograph',
                                     'negative': 'anime, manga, cartoon, illustration, painting, 3d render, '
                                                 'doll, plastic, airbrushed skin, overexposed, oversized '
                                                 'eyes, huge eyes, giant eyes, juvenile face, child, bad '
                                                 'anatomy, distorted fingers, text, logo, watermark, cropped '
                                                 'head',
                                     'width': 896,
                                     'height': 1152,
                                     'steps': 36,
                                     'cfg': 5.5,
                                     'weight': 0.6,
                                     'batchSize': 2,
                                     'seed': 2026102325,
                                     'sampler': 'dpmpp_2m',
                                     'scheduler': 'karras',
                                     'aspect': '896x1152'},
                      'inputs': [{'src': './assets/examples/cheer-face-reference.webp',
                                  'alt': '裁切後的臉部參考圖',
                                  'alt_en': 'Cropped face reference',
                                  'label': '裁切後的臉部參考圖',
                                  'label_en': 'Cropped face reference',
                                  'slot': 'face',
                                  'kind': 'image'}],
                      'evidence': {'kind': 'local-comfyui',
                                   'run_id': 'cheer-instantid-v3',
                                   'workflow': 'instantid_prompt',
                                   'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                                   'models': ['instantid/diffusion_pytorch_model.safetensors',
                                              'ip-adapter.bin',
                                              'sd_xl_base_1.0.safetensors'],
                                   'prompt': 'RAW candid close-up photograph of a 26-year-old adult '
                                             'Taiwanese woman, natural expression, realistic brown eyes, '
                                             'realistic human facial proportions, realistic adult face, fine '
                                             'natural skin pores, long dark hair, wearing a plain dark navy '
                                             'sports jacket, head and shoulders, evening baseball stadium '
                                             'with blurred warm lights, soft natural lighting, Nikon full '
                                             'frame camera, 85mm f1.8, detailed photograph',
                                   'negative_prompt': 'anime, manga, cartoon, illustration, painting, 3d '
                                                      'render, doll, plastic, airbrushed skin, overexposed, '
                                                      'oversized eyes, huge eyes, giant eyes, juvenile face, '
                                                      'child, bad anatomy, distorted fingers, text, logo, '
                                                      'watermark, cropped head',
                                   'duration_seconds': 16.334,
                                   'executed_at': '2026-10-22T20:10:20.505000+00:00',
                                   'seed': 2026102325,
                                   'steps': 36,
                                   'cfg': 5.5,
                                   'weight': 0.6,
                                   'sampler': 'dpmpp_2m',
                                   'scheduler': 'karras',
                                   'width': 896,
                                   'height': 1152},
                      'output': {'kind': 'image',
                                 'images': [{'src': './assets/examples/cheer-instantid.webp',
                                             'alt': '臉部參考：夜間球場肖像候選；此為實際 GPU 輸出。',
                                             'alt_en': 'Face reference: evening stadium portrait candidates; '
                                                       'actual GPU output.'},
                                            {'src': './assets/examples/cheer-instantid-2.webp',
                                             'alt': '同一次實測的肖像候選 2',
                                             'alt_en': 'Portrait candidate 2 from the same recorded run'}],
                                 'run_id': 'cheer-instantid-v3'},
                      'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。 '
                              '保留模型原始候選，臉部細節與寫實度仍須自行挑選；沒有後製修臉。',
                      'note_en': 'Recorded on a local RTX 5090, not generated from your input on this '
                                 'website. The performer is an original fictional adult. These are '
                                 'unretouched model candidates; facial detail and realism still require '
                                 'selection.'},
 'img2video': {'title': '走近鏡頭，揮手打招呼',
               'title_en': 'Walk toward the camera and wave hello',
               'prompt': 'The adult woman takes two small natural steps towards the camera, smiles brightly, '
                         'raises her right hand and waves hello twice. Her ponytail and jersey move '
                         'naturally. Friendly confident adult baseball cheer performer. The camera stays '
                         'still, full body in frame, consistent face and clothing, realistic fluid human '
                         'motion.',
               'prompt_en': 'The adult woman takes two small natural steps towards the camera, smiles '
                            'brightly, raises her right hand and waves hello twice. Her ponytail and jersey '
                            'move naturally. Friendly confident adult baseball cheer performer. The camera '
                            'stays still, full body in frame, consistent face and clothing, realistic fluid '
                            'human motion.',
               'prefill': {'positive': 'The adult woman takes two small natural steps towards the camera, '
                                       'smiles brightly, raises her right hand and waves hello twice. Her '
                                       'ponytail and jersey move naturally. Friendly confident adult '
                                       'baseball cheer performer. The camera stays still, full body in '
                                       'frame, consistent face and clothing, realistic fluid human motion.',
                           'negative': 'static, frozen, camera pan, camera zoom, distorted face, deformed '
                                       'hands, extra limbs, duplicate person, blurry, text, logo, running',
                           'width': 480,
                           'height': 720,
                           'length': 81,
                           'fps': 16,
                           'steps': 6,
                           'cfg': 1,
                           'shift': 8,
                           'loraStrength': 1,
                           'seed': 2026102306,
                           'sampler': 'euler',
                           'scheduler': 'simple',
                           'aspect': '480x720'},
               'prefill_en': {'positive': 'The adult woman takes two small natural steps towards the camera, '
                                          'smiles brightly, raises her right hand and waves hello twice. Her '
                                          'ponytail and jersey move naturally. Friendly confident adult '
                                          'baseball cheer performer. The camera stays still, full body in '
                                          'frame, consistent face and clothing, realistic fluid human '
                                          'motion.',
                              'negative': 'static, frozen, camera pan, camera zoom, distorted face, deformed '
                                          'hands, extra limbs, duplicate person, blurry, text, logo, running',
                              'width': 480,
                              'height': 720,
                              'length': 81,
                              'fps': 16,
                              'steps': 6,
                              'cfg': 1,
                              'shift': 8,
                              'loraStrength': 1,
                              'seed': 2026102306,
                              'sampler': 'euler',
                              'scheduler': 'simple',
                              'aspect': '480x720'},
               'inputs': [{'src': './assets/examples/cheer-reference.webp',
                           'alt': '原始角色參考圖',
                           'alt_en': 'Original performer reference',
                           'label': '原始角色參考圖',
                           'label_en': 'Original performer reference',
                           'slot': 'image',
                           'kind': 'image'}],
               'evidence': {'kind': 'local-comfyui',
                            'run_id': 'cheer-wave-wan-v1',
                            'workflow': 'wan_i2v_prompt',
                            'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                            'models': ['Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors',
                                       'clip_vision_h.safetensors',
                                       'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
                                       'wan2.1_i2v_480p_14B_fp8_scaled.safetensors',
                                       'wan_2.1_vae.safetensors'],
                            'prompt': 'The adult woman takes two small natural steps towards the camera, '
                                      'smiles brightly, raises her right hand and waves hello twice. Her '
                                      'ponytail and jersey move naturally. Friendly confident adult baseball '
                                      'cheer performer. The camera stays still, full body in frame, '
                                      'consistent face and clothing, realistic fluid human motion.',
                            'negative_prompt': 'static, frozen, camera pan, camera zoom, distorted face, '
                                               'deformed hands, extra limbs, duplicate person, blurry, text, '
                                               'logo, running',
                            'duration_seconds': 61.1,
                            'executed_at': '2026-10-22T20:03:52.731000+00:00',
                            'seed': 2026102306,
                            'steps': 6,
                            'cfg': 1,
                            'sampler': 'euler',
                            'scheduler': 'simple',
                            'length': 81,
                            'fps': 16,
                            'width': 480,
                            'height': 720},
               'output': {'kind': 'video',
                          'images': [],
                          'video': {'src': './assets/examples/cheer-wave.mp4',
                                    'poster': './assets/examples/cheer-wave-poster.webp'},
                          'run_id': 'cheer-wave-wan-v1'},
               'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。 影片無聲；動作由模型生成，沒有使用第三方舞蹈影片或音樂。',
               'note_en': 'Recorded on a local RTX 5090, not generated from your input on this website. The '
                          'performer is an original fictional adult. The video is silent; movement was '
                          'generated without third-party dance footage or music.'},
 'dance': {'title': '原創應援動作轉移：紅色球衣角色',
           'title_en': 'Original cheer motion transfer: red-uniform character',
           'prompt': 'An adult Taiwanese baseball cheer performer in a red jersey and navy athletic shorts, '
                     'doing simple cheerful side steps and raising her arms following the reference '
                     'movement. Natural realistic human motion, full body, consistent identity and clothing, '
                     'stationary camera.',
           'prompt_en': 'An adult Taiwanese baseball cheer performer in a red jersey and navy athletic '
                        'shorts, doing simple cheerful side steps and raising her arms following the '
                        'reference movement. Natural realistic human motion, full body, consistent identity '
                        'and clothing, stationary camera.',
           'prefill': {'mode': 'animate',
                       'positive': 'An adult Taiwanese baseball cheer performer in a red jersey and navy '
                                   'athletic shorts, doing simple cheerful side steps and raising her arms '
                                   'following the reference movement. Natural realistic human motion, full '
                                   'body, consistent identity and clothing, stationary camera.',
                       'negative': 'static, frozen, distorted face, deformed hands, extra limbs, duplicate '
                                   'person, blurry, text, logo',
                       'length': 81,
                       'contextLength': 81,
                       'fps': 16,
                       'steps': 6,
                       'cfg': 1,
                       'shift': 5,
                       'poseStrength': 1,
                       'loraStrength': 1,
                       'seed': 2026102308,
                       'sampler': 'euler',
                       'scheduler': 'simple'},
           'prefill_en': {'mode': 'animate',
                          'positive': 'An adult Taiwanese baseball cheer performer in a red jersey and navy '
                                      'athletic shorts, doing simple cheerful side steps and raising her '
                                      'arms following the reference movement. Natural realistic human '
                                      'motion, full body, consistent identity and clothing, stationary '
                                      'camera.',
                          'negative': 'static, frozen, distorted face, deformed hands, extra limbs, '
                                      'duplicate person, blurry, text, logo',
                          'length': 81,
                          'contextLength': 81,
                          'fps': 16,
                          'steps': 6,
                          'cfg': 1,
                          'shift': 5,
                          'poseStrength': 1,
                          'loraStrength': 1,
                          'seed': 2026102308,
                          'sampler': 'euler',
                          'scheduler': 'simple'},
           'inputs': [{'src': './assets/examples/cheer-red-reference.webp',
                       'alt': '改圖工作流產生的紅衣角色',
                       'alt_en': 'Red-uniform character from the verified edit',
                       'label': '改圖工作流產生的紅衣角色',
                       'label_en': 'Red-uniform character from the verified edit',
                       'slot': 'reference',
                       'kind': 'image'},
                      {'src': './assets/examples/cheer-motion-source.mp4',
                       'alt': '自行生成的應援動作影片',
                       'alt_en': 'Original generated cheer motion',
                       'label': '自行生成的應援動作影片',
                       'label_en': 'Original generated cheer motion',
                       'slot': 'video',
                       'kind': 'video',
                       'poster': './assets/examples/cheer-motion-source-poster.webp'}],
           'evidence': {'kind': 'local-comfyui',
                        'run_id': 'cheer-scail-v1',
                        'workflow': 'scail_dance_prompt',
                        'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                        'models': ['Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors',
                                   'clip_vision_h.safetensors',
                                   'sam3.1_multiplex_fp16.safetensors',
                                   'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
                                   'wan2.1_14B_SCAIL_2_fp8_scaled.safetensors',
                                   'wan_2.1_vae.safetensors'],
                        'prompt': 'An adult Taiwanese baseball cheer performer in a red jersey and navy '
                                  'athletic shorts, doing simple cheerful side steps and raising her arms '
                                  'following the reference movement. Natural realistic human motion, full '
                                  'body, consistent identity and clothing, stationary camera.',
                        'negative_prompt': 'static, frozen, distorted face, deformed hands, extra limbs, '
                                           'duplicate person, blurry, text, logo',
                        'duration_seconds': 82.01,
                        'executed_at': '2026-10-22T20:08:16.152000+00:00',
                        'seed': 2026102308,
                        'steps': 6,
                        'cfg': 1,
                        'sampler': 'euler',
                        'scheduler': 'simple',
                        'length': 81,
                        'fps': 16,
                        'width': 480,
                        'height': 736},
           'output': {'kind': 'video',
                      'images': [],
                      'video': {'src': './assets/examples/cheer-scail.mp4',
                                'poster': './assets/examples/cheer-scail-poster.webp'},
                      'run_id': 'cheer-scail-v1'},
           'note': '這是已在本機 RTX 5090 完成的實測記錄；此網站只重播成果，不會依你輸入即時生成。角色為原創虛構成年人。 影片無聲；動作由模型生成，沒有使用第三方舞蹈影片或音樂。',
           'note_en': 'Recorded on a local RTX 5090, not generated from your input on this website. The '
                      'performer is an original fictional adult. The video is silent; movement was generated '
                      'without third-party dance footage or music.'},
 'character-create': {'title': '建立虛構成人應援角色「晴晴」',
                      'title_en': 'Create fictional adult cheer performer “Qing”',
                      'prompt': '晴晴，26 歲的虛構台灣應援角色，青綠白球衣、馬尾、自然親切的笑容。',
                      'prompt_en': 'Qing, a fictional 26-year-old Taiwanese cheer performer in a '
                                   'teal-and-white jersey, ponytail and friendly smile.',
                      'prefill': {'name': '晴晴', 'description': '26 歲的虛構台灣應援角色；原創服裝與人物，非真人隊員。'},
                      'prefill_en': {'name': 'Qing',
                                     'description': 'Fictional 26-year-old Taiwanese cheer performer; '
                                                    'original clothing and identity, not a real team '
                                                    'member.'},
                      'inputs': [{'src': './assets/examples/cheer-reference.webp',
                                  'alt': '可匯入本機的原創角色參考圖',
                                  'alt_en': 'Original character reference for your local installation',
                                  'slot': 'photos',
                                  'kind': 'image'}],
                      'evidence': {'kind': 'interface-simulation'},
                      'output': {'kind': 'character', 'images': []},
                      'note': '依表單內容組成示範角色卡；參考圖不會依描述生成，也不會保存到帳戶或資料庫。',
                      'note_en': 'A simulated card uses your form values. Its reference image is not generated from your input; nothing is saved to an account or database.'}}
