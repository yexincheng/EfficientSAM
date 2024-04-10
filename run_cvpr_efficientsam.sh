#!/bin/bash -l
#SBATCH --nodes 1
#SBATCH --job-name=efficientsam_cvpr2
#SBATCH --ntasks 1
#SBATCH -c 50
#SBATCH --mem=50000
#SBATCH -o efficientsam_cvpr_out2.txt
#SBATCH -e efficientsam_cvpr_error2.txt
#SBATCH --partition=general
#SBTACH --account=a_barth
#SBATCH --time=48:00:00
#SBATCH --constraint=epyc4
#SBATCH --batch=epyc4


source activate medsam
srun python CVPR24_EfficientSAM_infer.py --data_root /scratch/project/bollmann_lab/MedSAM_Laptop/datasets/validation/imgs \
                                --pred_save_dir /scratch/project/bollmann_lab/MedSAM_Laptop/datasets/validation/segs_efficientsam \
                                --png_save_dir /scratch/project/bollmann_lab/MedSAM_Laptop/datasets/validation/overlay_efficientsam  