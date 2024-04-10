import matplotlib.pyplot as plt
import numpy as np
import torch
from torchvision.transforms import ToTensor
from collections import OrderedDict
import os
from efficient_sam.build_efficient_sam import build_efficient_sam_vitt, build_efficient_sam_vits
import cv2
from os.path import join, isfile, basename
from time import time
from datetime import datetime
from glob import glob
import pandas as pd
from tqdm import tqdm
import argparse
import gc

def show_mask(mask, ax, mask_color=None, alpha=0.5):
    """
    show mask on the image

    Parameters
    ----------
    mask : numpy.ndarray
        mask of the image
    ax : matplotlib.axes.Axes
        axes to plot the mask
    mask_color : numpy.ndarray
        color of the mask
    alpha : float
        transparency of the mask
    """
    if mask_color is not None:
        color = np.concatenate([mask_color, np.array([alpha])], axis=0)
    else:
        color = np.array([251/255, 252/255, 30/255, alpha])
    h, w = mask.shape[-2:]
    mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
    ax.imshow(mask_image)

def show_box(box, ax, edgecolor='blue'):
    """
    show bounding box on the image

    Parameters
    ----------
    box : numpy.ndarray
        bounding box coordinates in the original image
    ax : matplotlib.axes.Axes
        axes to plot the bounding box
    edgecolor : str
        color of the bounding box
    """
    x0, y0 = box[0], box[1]
    w, h = box[2] - box[0], box[3] - box[1]
    ax.add_patch(plt.Rectangle((x0, y0), w, h, edgecolor=edgecolor, facecolor=(0,0,0,0), lw=2))     

def get_bbox(mask, bbox_shift=3):
    """
    Get the bounding box coordinates from the mask (256x256)

    Parameters
    ----------
    mask_1024 : numpy.ndarray
        the mask of the resized image

    bbox_shift : int
        Add perturbation to the bounding box coordinates
    
    Returns
    -------
    numpy.ndarray
        bounding box coordinates in the resized image
    """
    y_indices, x_indices = np.where(mask > 0)
    x_min, x_max = np.min(x_indices), np.max(x_indices)
    y_min, y_max = np.min(y_indices), np.max(y_indices)
    # add perturbation to bounding box coordinates and test the robustness
    # this can be removed if you do not want to test the robustness
    H, W = mask.shape
    x_min = max(0, x_min - bbox_shift)
    x_max = min(W, x_max + bbox_shift)
    y_min = max(0, y_min - bbox_shift)
    y_max = min(H, y_max + bbox_shift)

    bboxes = np.array([x_min, y_min, x_max, y_max])

    return bboxes

@torch.no_grad()
def efficientsam_infer(embddings, box, model, H, W):
    input_label = np.array([2,3])
    
    pts_sampled = torch.reshape(torch.tensor(box), [1, 1, -1, 2])
    pts_labels = torch.reshape(torch.tensor(input_label), [1, 1, -1])
    predicted_logits, predicted_iou = model.predict_masks(embddings, pts_sampled, pts_labels, True, H,W, H, W)
    sorted_ids = torch.argsort(predicted_iou, dim=-1, descending=True)
    predicted_iou = torch.take_along_dim(predicted_iou, sorted_ids, dim=2)
    predicted_logits = torch.take_along_dim(
        predicted_logits, sorted_ids[..., None, None], dim=2
    )

    return torch.ge(predicted_logits[0, 0, 0, :, :], 0).cpu().detach().numpy()


def EfficientSAM_infer_npz_2D(efficient_sam_model, img_npz_file, pred_save_dir, save_overlay, png_save_dir):
    npz_name = basename(img_npz_file)
    npz_data = np.load(img_npz_file, 'r', allow_pickle=True) # (H, W, 3)
    img_3c = npz_data['imgs'] # (H, W, 3)
    print(f'input data shape: {img_3c.shape}')
    # assert np.max(img_3c)<256, f'input data should be in range [0, 255], but got {np.unique(img_3c)}'
    H, W = img_3c.shape[:2]
    boxes = npz_data['boxes']
    segs = np.zeros(img_3c.shape[:2], dtype=np.uint8)

    img_tensor = ToTensor()(img_3c)
    img_tensor = img_tensor[None, ...]
    # print(f'input tensor shape: {img_tensor.shape}')
    ## preprocessing
    img_1024 = efficient_sam_model.preprocess(img_tensor)
    # print(f'preprocessed data shape: {img_1024.shape}')

    with torch.no_grad():
        image_embedding = efficient_sam_model.image_encoder(img_1024)

    for idx, box in enumerate(boxes, start=1):
        mask = efficientsam_infer(image_embedding, box, efficient_sam_model, H,W)
        segs[mask>0] = idx

    np.savez_compressed(
        join(pred_save_dir, npz_name),
        segs=segs,
    )
    if save_overlay:
        fig, ax = plt.subplots(1, 2, figsize=(10, 5))
        ax[0].imshow(img_3c)
        ax[1].imshow(img_3c)
        ax[0].set_title("Image")
        ax[1].set_title("EfficientSAM Segmentation")
        # ax[0].axis('off')
        # ax[1].axis('off')

        for i, box in enumerate(boxes):
            color = np.random.rand(3)
            box_viz = box
            show_box(box_viz, ax[1], edgecolor=color)
            show_mask((segs == i+1).astype(np.uint8), ax[1], mask_color=color)

        plt.tight_layout()
        plt.savefig(join(png_save_dir, npz_name.split(".")[0] + '.png'), dpi=300)
        plt.close()

def EfficientSAM_infer_npz_3D(efficient_sam_model, img_npz_file, pred_save_dir, save_overlay, png_save_dir):
    npz_name = basename(img_npz_file)
    npz_data = np.load(img_npz_file, 'r', allow_pickle=True) # (H, W, 3)
    img_3D = npz_data['imgs'] # (D, H, W)
    print(f'input data shape: {img_3D.shape}')
    spacing = npz_data['spacing'] # not used in this demo because it treats each slice independently
    segs = np.zeros_like(img_3D, dtype=np.uint8) 
    boxes_3D = npz_data['boxes'] # [[x_min, y_min, z_min, x_max, y_max, z_max]]

    for idx, box3D in enumerate(boxes_3D, start=1):
        segs_3d_temp = np.zeros_like(img_3D, dtype=np.uint8) 
        x_min, y_min, z_min, x_max, y_max, z_max = box3D
        assert z_min < z_max, f"z_min should be smaller than z_max, but got {z_min=} and {z_max=}"
        mid_slice_bbox_2d = np.array([x_min, y_min, x_max, y_max])
        z_middle = int((z_max - z_min)/2 + z_min)
        # print(npz_name, 'infer from middle slice to the z_max')
        for z in range(z_middle, z_max):
            img_2d = img_3D[z, :, :]
            if len(img_2d.shape) == 2:
                img_3c = np.repeat(img_2d[:, :, None], 3, axis=-1)
            else:
                img_3c = img_2d
            H, W, _ = img_3c.shape
            img_tensor = ToTensor()(img_3c)
            img_tensor = img_tensor[None, ...]
            img_1024 = efficient_sam_model.preprocess(img_tensor)

            # get the image embedding
            with torch.no_grad():
                image_embedding = efficient_sam_model.image_encoder(img_1024) # (1, 256, 64, 64)
            
            if z != z_middle:
                pre_seg = segs[z-1, :, :]
                if np.max(pre_seg) > 0:
                    box = get_bbox(pre_seg)
                else:
                    box = mid_slice_bbox_2d
            else:
                box = mid_slice_bbox_2d
            mask = efficientsam_infer(image_embedding, box, efficient_sam_model, H,W)
            segs_3d_temp[z, mask>0] = idx
        
        # infer from middle slice to the z_max
        # print(npz_name, 'infer from middle slice to the z_min')
        for z in range(z_middle-1, z_min, -1):
            img_2d = img_3D[z, :, :]
            if len(img_2d.shape) == 2:
                img_3c = np.repeat(img_2d[:, :, None], 3, axis=-1)
            else:
                img_3c = img_2d
            H, W, _ = img_3c.shape

            img_tensor = ToTensor()(img_3c) 
            img_tensor = img_tensor[None, ...]
            img_1024 = efficient_sam_model.preprocess(img_tensor)
            # get the image embedding
            with torch.no_grad():
                image_embedding = efficient_sam_model.image_encoder(img_1024) # (1, 256, 64, 64)

            pre_seg = segs[z+1, :, :]
            if np.max(pre_seg) > 0:
                box = get_bbox(pre_seg)
            else:
                box = mid_slice_bbox_2d
            mask = efficientsam_infer(image_embedding, box, efficient_sam_model, H,W)
            segs_3d_temp[z, mask>0] = idx
        segs[segs_3d_temp>0] = idx
    np.savez_compressed(
        join(pred_save_dir, npz_name),
        segs=segs,
    )
    # visualize image, mask and bounding box
    if save_overlay:
        idx = int(segs.shape[0] / 2)
        fig, ax = plt.subplots(1, 2, figsize=(10, 5))
        ax[0].imshow(img_3D[idx], cmap='gray')
        ax[1].imshow(img_3D[idx], cmap='gray')
        ax[0].set_title("Image")
        ax[1].set_title("MedSAM Segmentation")
        ax[0].axis('off')
        ax[1].axis('off')

        for i, box3D in enumerate(boxes_3D, start=1):
            if np.sum(segs[idx]==i) > 0:
                color = np.random.rand(3)
                x_min, y_min, z_min, x_max, y_max, z_max = box3D
                box_viz = np.array([x_min, y_min, x_max, y_max])
                show_box(box_viz, ax[1], edgecolor=color)
                show_mask(segs[idx]==i, ax[1], mask_color=color)

        plt.tight_layout()
        plt.savefig(join(png_save_dir, npz_name.split(".")[0] + '.png'), dpi=300)
        plt.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='EfficientSAM inference')
    parser.add_argument('--data_root', type=str, default='data', help='root directory of the data')
    parser.add_argument('--pred_save_dir', type=str, default='pred', help='directory to save the prediction')
    parser.add_argument('--save_overlay', type=bool, default=True, help='whether to save the overlay image')
    parser.add_argument('--png_save_dir', type=str, default='png', help='directory to save the overlay image')
    parser.add_argument('--device', type=str, default="cpu", help='device to run the inference')
    args = parser.parse_args()
    
    os.makedirs(args.pred_save_dir, exist_ok=True)
    if args.save_overlay:
        assert args.png_save_dir is not None, "Please specify the directory to save the overlay image"
        os.makedirs(args.png_save_dir, exist_ok=True)

    torch.set_float32_matmul_precision('high')
    torch.manual_seed(2024)
    torch.cuda.manual_seed(2024)
    np.random.seed(2024)

    img_npz_files = sorted(glob(join(args.data_root, '*.npz'), recursive=True))
    done = [basename(f) for f in sorted(glob(join(args.pred_save_dir, '*.npz')))]
    print(len(done))
    efficiency = OrderedDict()
    efficiency['case'] = []
    efficiency['time'] = []

    efficient_sam_model = build_efficient_sam_vitt()
    # Since EfficientSAM-S checkpoint file is >100MB, we store the zip file.
    # with zipfile.ZipFile("weights/efficient_sam_vits.pt.zip", 'r') as zip_ref:
    #     zip_ref.extractall("weights")
    # efficient_sam_vits_model = build_efficient_sam_vits()

    efficient_sam_model.to(args.device)
    efficient_sam_model.eval()

    for img_npz_file in tqdm(img_npz_files):
        start_time = time()
        gc.collect()

        if basename(img_npz_file).startswith('3D'):
            EfficientSAM_infer_npz_3D(efficient_sam_model, img_npz_file, args.pred_save_dir, args.save_overlay, args.png_save_dir)
        else:
            EfficientSAM_infer_npz_2D(efficient_sam_model, img_npz_file, args.pred_save_dir, args.save_overlay, args.png_save_dir)
        end_time = time()
        efficiency['case'].append(basename(img_npz_file))
        efficiency['time'].append(end_time - start_time)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(current_time, 'file name:', basename(img_npz_file), 'time cost:', np.round(end_time - start_time, 4))
        # break
    efficiency_df = pd.DataFrame(efficiency)
    efficiency_df.to_csv(join(args.pred_save_dir, 'efficiency2.csv'), index=False)
