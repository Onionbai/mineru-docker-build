import torch
import filetype
import json
import litserve as ls
import os
import io
import zipfile
import shutil
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.responses import Response
from magic_pdf.tools.common import do_parse
from magic_pdf.model.doc_analyze_by_custom_model import ModelSingleton


class MinerUAPI(ls.LitAPI):
    def __init__(self, output_dir='./tmp'):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def clean_memory(device):
        import gc
        if torch.cuda.is_available():
            with torch.cuda.device(device):
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        gc.collect()

    def setup(self, device):
        with patch('magic_pdf.model.doc_analyze_by_custom_model.get_device') as mock_obj:
            mock_obj.return_value = device
            model_manager = ModelSingleton()
            model_manager.get_model(True, False)
            model_manager.get_model(False, False)
            mock_obj.assert_called()
            print(f'Model initialization complete!')
    def decode_request(self, request):
        # 获取上传文件
        uploaded_file = request['file']
        file_content = uploaded_file.file.read()
        filename = uploaded_file.filename
        
        # 解析参数
        kwargs = json.loads(request['kwargs'])  # 自动包含所有开关参数
        
        # 参数类型转换（如果需要）
        bool_keys = ['f_dump_md', 'f_draw_layout_bbox', 'f_dump_orig_pdf', 'f_draw_span_bbox']
        for key in bool_keys:
            if key in kwargs:
                kwargs[key] = bool(kwargs[key])

        # 文件类型验证
        if filetype.guess_mime(file_content) != 'application/pdf':
            raise HTTPException(400, "Invalid file type (must be PDF after conversion)")
            
        return file_content, kwargs, filename

    def decode_request0(self, request):
        uploaded_file = request['file']
        file_content = uploaded_file.file.read()
        filename = uploaded_file.filename  # 获取原始文件名
        kwargs = json.loads(request['kwargs'])
        
        # 验证文件类型是否为PDF（客户端已转换）
        if filetype.guess_mime(file_content) != 'application/pdf':
            raise HTTPException(400, "Invalid file type (must be PDF after conversion)")
            
        return file_content, kwargs, filename

    def predict(self, inputs):
        try:
            file_content, kwargs, filename = inputs
            # 生成基于原始文件名的目录名
            base_name = os.path.splitext(os.path.basename(filename))[0]
            output_dir_path = os.path.join(self.output_dir, base_name)
            
            # 执行解析
            do_parse(self.output_dir, base_name, file_content, [], **kwargs)
            
            # 打包结果到ZIP
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(output_dir_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, start=self.output_dir)
                        zipf.write(file_path, arcname)
            buffer.seek(0)
            zip_data = buffer.getvalue()
            
            # 清理临时文件
            shutil.rmtree(output_dir_path)
            
            return zip_data
        except Exception as e:
            raise HTTPException(500, detail=f'{e}')
        finally:
            self.clean_memory(self.device)

    def encode_response(self, response):
        return Response(
            content=response,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="output.zip"'}
        )


if __name__ == '__main__':
    server = ls.LitServer(MinerUAPI(), accelerator='gpu', devices='auto', timeout=False)
    server.run(port=8999)

    