"""生成 gNMI protobuf 代码。

运行：python -m scripts.gen_proto
生成物：app/proto/gnmi_pb2.py、app/proto/gnmi_pb2_grpc.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROTO_DIR = Path(__file__).resolve().parents[1] / "app" / "proto"
PROTO_FILE = PROTO_DIR / "gnmi.proto"


def main() -> int:
    if not PROTO_FILE.exists():
        print(f"未找到 {PROTO_FILE}")
        return 1
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={PROTO_DIR}",
        f"--grpc_python_out={PROTO_DIR}",
        str(PROTO_FILE),
    ]
    print("执行:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("生成失败:\n", result.stderr)
        return result.returncode
    # grpc 生成的文件 import 方式为 `import gnmi_pb2 as gnmi__pb2`，需改成包内相对导入
    grpc_file = PROTO_DIR / "gnmi_pb2_grpc.py"
    if grpc_file.exists():
        content = grpc_file.read_text(encoding="utf-8")
        content = content.replace(
            "import gnmi_pb2 as gnmi__pb2",
            "from app.proto import gnmi_pb2 as gnmi__pb2",
        )
        grpc_file.write_text(content, encoding="utf-8")
        print("已修正 grpc 生成文件的导入路径")
    for f in sorted(PROTO_DIR.glob("gnmi_pb2*.py")):
        print(f"生成: {f.name} ({f.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
