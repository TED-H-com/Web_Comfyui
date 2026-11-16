"""Reviewed real ComfyUI examples. Evidence lives in webui/docs/examples/provenance.json."""
RECORDED_DEMOS = {
 'image-blend': {'title': '保留人物原貌：換成夜間球場背景',
                 'title_en': 'Keep the original person: switch to an evening stadium',
                 'prompt': 'person',
                 'prompt_en': 'person',
                 'prefill': {'positive': 'person', 'threshold': 0.5},
                 'prefill_en': {'positive': 'person', 'threshold': 0.5},
                 'inputs': [{'src': './assets/examples/cheer-subject-reference.webp',
                             'alt': '保留原貌的原始人物',
                             'alt_en': 'Original subject to preserve',
                             'label': '人物原圖：保留臉、服裝與姿勢',
                             'label_en': 'Original subject: preserve face, clothing and pose',
                             'slot': 'image1',
                             'kind': 'image'},
                            {'src': './assets/examples/stadium-ground-v2-reference.webp',
                             'alt': '生成的夜間球場背景',
                             'alt_en': 'Generated evening stadium background',
                             'label': '目標背景：夜間球場',
                             'label_en': 'Target background: evening stadium',
                             'slot': 'image2',
                             'kind': 'image'}],
                 'evidence': {'kind': 'local-comfyui',
                              'run_id': 'cheer-subject-blend-v4',
                              'workflow': 'subject_blend_prompt',
                              'device': 'NVIDIA GeForce RTX 5090 (32 GB)',
                              'models': ['sam3.1_multiplex_fp16.safetensors'],
                              'prompt': 'person',
                              'threshold': 0.5,
                              'duration_seconds': 1.066,
                              'executed_at': '2026-10-22T21:00:02.032000+00:00',
                              'width': 1024,
                              'height': 1536},
                 'output': {'kind': 'image',
                            'images': [{'src': './assets/examples/cheer-subject-blend.webp',
                                        'alt': 'GPU 分割人物後換背景；保留原圖的臉孔、服裝與姿勢。',
                                        'alt_en': 'GPU subject segmentation and background replacement; the '
                                                  'original face, clothing and pose are preserved.'}],
                            'run_id': 'cheer-subject-blend-v4',
                            'comparison': True},
                 'note': 'RTX 5090 實跑：SAM3 '
                         '分割人物後直接合成原圖像素，不重畫臉。已比對人物區域，保留原圖人物，只更換背景；不會自動補光或生成陰影。此網站重播固定成果，不會依你的輸入即時生成。',
                 'note_en': 'Recorded on an RTX 5090, not generated from your input on this website. SAM3 '
                            'segments the subject and composites original pixels without redrawing the face. '
                            'Subject pixels were compared with the original; only the background is '
                            'replaced, with small image decode/save quantization. No automatic relighting or '
                            'shadows.'},
 'character-create': {'title': '建立角色與素材工作區',
                      'title_en': 'Create a character and media workspace',
                      'prompt': '晴晴，26 歲的虛構台灣應援角色，青綠白球衣、馬尾、自然親切的笑容。',
                      'prompt_en': 'Qing, a fictional 26-year-old Taiwanese cheer performer in a '
                                   'teal-and-white jersey, ponytail and friendly smile.',
                      'prefill': {'name': '晴晴', 'description': '26 歲的虛構台灣應援角色；原創服裝與人物，非真人隊員。'},
                      'prefill_en': {'name': 'Qing',
                                     'description': 'Fictional 26-year-old Taiwanese cheer performer; '
                                                    'original clothing and identity, not a real team '
                                                    'member.'},
                      'inputs': [{'src': './assets/examples/cheer-reference.webp',
                                  'label': '範例參考圖・尚未建立角色',
                                  'label_en': 'Example reference · character not created yet',
                                  'alt': '原創角色範例參考圖，尚未建立角色',
                                  'alt_en': 'Original example reference; character not created yet',
                                  'slot': 'photos',
                                  'kind': 'image'}],
                      'evidence': {'kind': 'interface-simulation'},
                      'output': {'kind': 'character', 'images': []},
                      'note': '建立後進入角色工作區，查看預先實跑的換裝、肖像與影片案例。角色暫存於此分頁，重新整理或清除示範後消失；不會上傳照片或訓練模型。',
                      'note_en': 'Creation opens a character workspace with prerecorded outfit, portrait and '
                                 'video examples, not generated from your input. Characters stay in this tab '
                                 'until refresh or reset; nothing is saved to an account or database.'},
}
