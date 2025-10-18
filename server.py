import os
import base64
import tempfile
from pathlib import Path
import litserve as ls
from fastapi import HTTPException
from fastapi.responses import Response
from loguru import logger
import gzip
import shutil
import tempfile

# 1. 导入 do_parse 和 MakeMode
from mineru.cli.common import do_parse, MakeMode
from mineru.utils.config_reader import get_device
from mineru.utils.model_utils import get_vram
import os
os.environ['MINERU_MODEL_SOURCE'] = 'local'

class MinerUAPI(ls.LitAPI):
    def __init__(self, output_dir='/tmp'):
        super().__init__()
        self.output_root_dir = Path(output_dir)

    def setup(self, device):
        """Setup environment variables exactly like MinerU CLI does"""
        logger.info(f"Setting up on device: {device}")
        
        if os.getenv('MINERU_DEVICE_MODE', None) == None:
            os.environ['MINERU_DEVICE_MODE'] = device if device != 'auto' else get_device()

        device_mode = os.environ['MINERU_DEVICE_MODE']
        if os.getenv('MINERU_VIRTUAL_VRAM_SIZE', None) == None:
            if device_mode.startswith("cuda") or device_mode.startswith("npu"):
                vram = round(get_vram(device_mode))
                os.environ['MINERU_VIRTUAL_VRAM_SIZE'] = str(vram)
            else:
                os.environ['MINERU_VIRTUAL_VRAM_SIZE'] = '1'
        logger.info(f"MINERU_VIRTUAL_VRAM_SIZE: {os.environ['MINERU_VIRTUAL_VRAM_SIZE']}")
		


    def decode_request(self, request):
        """
        [已更新] Decode file and *all* options from request.
        """
        file_b64 = request['file']
        options = request.get('options', {})
        is_compressed = request.get('compressed', False)
        
        file_name = request.get('file_name')
        logger.info(f"Received options: {options}")
        logger.info(f"parse_method from client: {options.get('parse_method', 'NOT_FOUND')}")
        if not file_name:
            import uuid
            file_name = f"{uuid.uuid4()}.pdf" 
            logger.warning("No file_name provided by client, generating a random one.")
        
        try:
            decoded_bytes = base64.b64decode(file_b64)
        except Exception as e:
            logger.error(f"Invalid base64 data received: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid base64 data: {e}")
        
        if is_compressed:
            try:
                file_bytes = gzip.decompress(decoded_bytes)
            except Exception as e:
                logger.error(f"Failed to decompress file: {e}")
                raise HTTPException(status_code=400, detail=f"Failed to decompress file: {e}")
        else:
            file_bytes = decoded_bytes

           

        # 2. [核心修改] 显式提取所有 do_parse 参数
        return {
            'file_bytes': file_bytes,
            'file_name': file_name,
            
            # --- do_parse 参数 ---
            'lang': options.get('lang', 'ch'), # 将在 predict 中被包装为 p_lang_list
            'backend': options.get('backend', 'pipeline'),
            
            # 关键命名映射：客户端发送 'method', do_parse 需要 'parse_method'
            'parse_method': options.get('parse_method', 'auto'), 
            
            'formula_enable': options.get('formula_enable', True),
            'table_enable': options.get('table_enable', True),
            'server_url': options.get('server_url', None),
            
            # Debug/Dump 标志
            'f_draw_layout_bbox': options.get('f_draw_layout_bbox', True),
            'f_draw_span_bbox': options.get('f_draw_span_bbox', True),
            'f_dump_md': options.get('f_dump_md', True),
            'f_dump_middle_json': options.get('f_dump_middle_json', True),
            
            # 关键命名映射：客户端发送 'f_dump_model_json', do_parse 需要 'f_dump_model_output'
            'f_dump_model_output': options.get('f_dump_model_output', True), 
            
            'f_dump_orig_pdf': options.get('f_dump_orig_pdf', True),
            
            # 新增参数 (来自您的签名, 客户端 ExtThread 尚不支持)
            'f_dump_content_list': options.get('f_dump_content_list', True), 
            'f_make_md_mode': options.get('f_make_md_mode', MakeMode.MM_MD), 
            
            # 页面范围
            'start_page_id': options.get('start_page_id', 0),
            'end_page_id': options.get('end_page_id', None),
        }

    def predict(self, inputs):
        """
        [已更新] Call MinerU's do_parse with *all* parameters.
        """
        file_bytes = inputs['file_bytes']
        file_name_stem = Path(inputs['file_name']).stem
        
        output_dir = self.output_root_dir / file_name_stem
        temp_dir = tempfile.gettempdir()
        zip_base_name = os.path.join(temp_dir, f"{file_name_stem}_{os.urandom(4).hex()}")
        zip_file_path = f"{zip_base_name}.zip"

        try:
            os.makedirs(output_dir, exist_ok=True)
            
            # 3. [核心修改] 显式调用所有 do_parse 参数
            do_parse(
                output_dir=str(output_dir),
                pdf_file_names=[file_name_stem],
                pdf_bytes_list=[file_bytes],
                p_lang_list=[inputs['lang']], # 从 'lang' 包装成 list
                
                # --- 所有参数列表 ---
                backend=inputs['backend'],
                parse_method=inputs['parse_method'],
                formula_enable=inputs['formula_enable'],
                table_enable=inputs['table_enable'],
                server_url=inputs['server_url'],
                f_draw_layout_bbox=inputs['f_draw_layout_bbox'],
                f_draw_span_bbox=inputs['f_draw_span_bbox'],
                f_dump_md=inputs['f_dump_md'],
                f_dump_middle_json=inputs['f_dump_middle_json'],
                f_dump_model_output=inputs['f_dump_model_output'],
                f_dump_orig_pdf=inputs['f_dump_orig_pdf'],
                f_dump_content_list=inputs['f_dump_content_list'],
                f_make_md_mode=inputs['f_make_md_mode'],
                start_page_id=inputs['start_page_id'],
                end_page_id=inputs['end_page_id']
                # --- 参数列表结束 ---
            )
            
            logger.debug(f"Zipping results from {output_dir} to {zip_file_path}")
            shutil.make_archive(
                base_name=zip_base_name,
                format='zip',
                root_dir=output_dir
            )
            
            with open(zip_file_path, 'rb') as f:
                zip_bytes = f.read()
            
            return zip_bytes
            
        except Exception as e:
            logger.error(f"Processing failed for {file_name_stem}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if Path(zip_file_path).exists():
                Path(zip_file_path).unlink()
                logger.debug(f"Cleaned up temp zip: {zip_file_path}")
            if Path(output_dir).exists():
                shutil.rmtree(output_dir)
                logger.debug(f"Cleaned up results dir: {output_dir}")

    def encode_response(self, response_bytes):
        """
        Encode the zip bytes as an application/zip file response.
        """
        logger.debug(f"Sending zip response, size: {len(response_bytes)} bytes")
        return Response(
            content=response_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=results.zip"}
        )

if __name__ == '__main__':
    server = ls.LitServer(
        MinerUAPI(output_dir='/tmp/mineru_output'),
        accelerator='auto',
        devices='auto',
        workers_per_device=1,
        timeout=False 
    )
    logger.info("Starting MinerU server on port 8000")
    server.run(port=8000, generate_client_file=False)
