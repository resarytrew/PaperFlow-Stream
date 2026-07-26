# opencv-python headless shim

`rapidocr-onnxruntime` declares a dependency on the GUI-enabled `opencv-python`
wheel. On many server-like Linux environments that wheel requires system OpenGL
libraries (`libGL.so.1`) and prevents even importing `cv2`.

Чистовик is a local API/server process and does not need OpenCV GUI bindings.
This tiny distribution is intentionally named `opencv-python` so pip's resolver
can satisfy RapidOCR's metadata, while the actual `cv2` module is provided by
`opencv-python-headless`.

Do not import anything from this package; it contains metadata only.
