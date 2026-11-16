# Face detector for the Tier 2 selfie check

`face_detection_yunet_2023mar.onnx` is YuNet, the light-weight face detector from
[OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
(MIT licence, see `LICENSE`). It returns a face box, five landmarks (eyes, nose
tip, mouth corners) and a score; `wallet/selfie.py` uses them to check a Tier 2
selfie before it is sent to the bank and to measure the head turn of the live
face check. It does not recognise anyone: the bank matches the face to the
BVN/NIN record.

- Size: 232,589 bytes
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
  (checked before the model is loaded; a different file disables face checks
  and is logged)
- Runtime: `opencv-python-headless` 4.x (`cv2.FaceDetectorYN`). This fixed-shape
  export is the one OpenCV 4.x DNN runs at any input size; OpenCV 5's ONNX
  Runtime engine needs the dynamic-shape export instead.
- Trained for faces of roughly 10 to 300 pixels, so photos are analysed at
  400 pixels on the long side.
