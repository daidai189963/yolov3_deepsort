import os
import cv2
import time
import argparse
import torch
import warnings
import numpy as np

from detector import build_detector
from deep_sort import build_tracker
from utils.draw import draw_boxes
from utils.parser import get_config
from utils.log import get_logger
from utils.io import write_results

# VideoTracker 目前包含的成员：
# cfg(YOLOv3 deepSORT)          args(video_path optional 参数:   )
# detector deepsort 实例对象 class name
# vdo 实例对象，用opencv打开video
# im_width im_height
# save_video_path save_result_path logger writer


class VideoTracker(object):
    def __init__(self, cfg, args, video_path):
        self.cfg = cfg
        self.args = args
        self.video_path = video_path
        self.logger = get_logger("root")

        use_cuda = args.use_cuda and torch.cuda.is_available()
        if not use_cuda:
            warnings.warn("Running in cpu mode which maybe very slow!", UserWarning)

        if args.display:
            cv2.namedWindow("test", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("test", args.display_width, args.display_height)

        if args.cam != -1:
            print("Using webcam " + str(args.cam))
            self.vdo = cv2.VideoCapture(args.cam)
        else:
            # 把一个ViedeoCapture 类 赋值给vdo 实例
            # 这个实例 包含 open method 可以
            self.vdo = cv2.VideoCapture()

        # 返回 yolov3 和 deepsort 的实例对象
        self.detector = build_detector(cfg, use_cuda=use_cuda)
        self.deepsort = build_tracker(cfg, use_cuda=use_cuda)
        self.class_names = self.detector.class_names # ????

    def __enter__(self):
        if self.args.cam != -1:
            ret, frame = self.vdo.read()
            assert ret, "Error: Camera error"
            self.im_width = frame.shape[0]
            self.im_height = frame.shape[1]

        else:
            # 开始读video
            assert os.path.isfile(self.video_path), "Path error"
            self.vdo.open(self.video_path)
            self.im_width = int(self.vdo.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.im_height = int(self.vdo.get(cv2.CAP_PROP_FRAME_HEIGHT))
            assert self.vdo.isOpened()

        if self.args.save_path:
            # 准备写 结果 ， 保存为 mjpg 文件
            os.makedirs(self.args.save_path, exist_ok=True)

            # path of saved video and results
            self.save_video_path = os.path.join(self.args.save_path, "results.avi")
            self.save_results_path = os.path.join(self.args.save_path, "results.txt")

            # create video writer
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            self.writer = cv2.VideoWriter(self.save_video_path, fourcc, 20, (self.im_width, self.im_height))

            # logging
            self.logger.info("Save results to {}".format(self.args.save_path))

        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if exc_type:
            print(exc_type, exc_value, exc_traceback)

    def run(self):
        results = []
        idx_frame = 0
        while self.vdo.grab():  # 将指针向后移一个 return true or false
            idx_frame += 1
            if idx_frame % self.args.frame_interval:  # frame_interval = 1 当frame_interval =2 时， 只有2的倍数才执行后面操作
                continue

            start = time.time()
            _, ori_im = self.vdo.retrieve()  # 输出 当前 指向的图像
            im = cv2.cvtColor(ori_im, cv2.COLOR_BGR2RGB)

            # do detection
            # bbox xywh class列的置信度 class列值
            bbox_xywh, cls_conf, cls_ids = self.detector(im)

            # select person class
            # yolo 会判断很多类型，其中 C ==0 的才是行人
            mask = cls_ids == 0

            bbox_xywh = bbox_xywh[mask]  # 把 行人的 bbox 提取出来
            # bbox dilation just in case bbox too small, delete this line if using a better pedestrian detector
            bbox_xywh[:, 3:] *= 1.2
            cls_conf = cls_conf[mask]

            # do tracking
            outputs = self.deepsort.update(bbox_xywh, cls_conf, im)

            # draw boxes for visualization
            if len(outputs) > 0:
                bbox_tlwh = []
                bbox_xyxy = outputs[:, :4]
                identities = outputs[:, -1]
                ori_im = draw_boxes(ori_im, bbox_xyxy, identities)

                for bb_xyxy in bbox_xyxy:
                    bbox_tlwh.append(self.deepsort._xyxy_to_tlwh(bb_xyxy))

                results.append((idx_frame - 1, bbox_tlwh, identities))

            end = time.time()

            if self.args.display:
                cv2.imshow("test", ori_im)
                cv2.waitKey(1)

            if self.args.save_path:
                self.writer.write(ori_im)

            # save results
            write_results(self.save_results_path, results, 'mot')

            # logging
            self.logger.info("time: {:.03f}s, fps: {:.03f}, detection numbers: {}, tracking numbers: {}" \
                             .format(end - start, 1 / (end - start), bbox_xywh.shape[0], len(outputs)))


# 在command line 中输入 python yolov3_deepsort.py C:/MyFlie/xxx.mp4 --display
# 这些 控制参数会存进sys.argv那里
# argparse 会从sys.argv那里解析出参数
# - 会被识别为 optional 例如 --config_detection --display
# 剩下的参数会被认为是位置参数，必须出现
def parse_args():
    parser = argparse.ArgumentParser()
    # parser.add_argument("VIDEO_PATH", type=str)  # 位置参数，必须写， 解析过后 VIDEO_PATH= 打入的路径
    parser.add_argument("--config_detection", type=str, default="./configs/yolov3.yaml")  # optional 没有出现的话，就是默认
    parser.add_argument("--config_deepsort", type=str, default="./configs/deep_sort.yaml")
    # parser.add_argument("--ignore_display", dest="display", action="store_false", default=True)
    parser.add_argument("--display", action="store_true")  # 出现 --display后，解析出来为 display = True
    parser.add_argument("--frame_interval", type=int, default=1)
    parser.add_argument("--display_width", type=int, default=800)
    parser.add_argument("--display_height", type=int, default=600)
    parser.add_argument("--save_path", type=str, default="./output/")
    parser.add_argument("--cpu", dest="use_cuda", action="store_false", default=True)  # 这个参数解析出来的名字为 use_cuda
    parser.add_argument("--camera", action="store", dest="cam", type=int, default="-1")
    return parser.parse_args()


if __name__ == "__main__":
    os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
    VIDEO_PATH = "C://Users//WXY//Desktop//视频//test_video.mp4"
    args = parse_args()
    cfg = get_config()  # return 了 一个空class instance
    cfg.merge_from_file(args.config_detection) # 调用自定义的method merge 别人的配置
    cfg.merge_from_file(args.config_deepsort) # 配置文件路径为：./configs/yolov3.yaml
    # with instance as variable
    # 首先执行 instance.__enter__() 返回值赋给 variable
    # 然后执行 vdo_trk.run()
    # 最后执行 instance.__exit__()
    # with VideoTracker(cfg, args, video_path=args.VIDEO_PATH) as vdo_trk:
    with VideoTracker(cfg, args, video_path=VIDEO_PATH) as vdo_trk:
        vdo_trk.run()
