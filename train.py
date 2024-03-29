import torch
import torch.nn as nn
from torchvision import transforms
from data_utils import SSDDataset, Transpose, Normalization, Normalization2, SSDDataAugmentation, collate_sample
from ssd_loss import SSDLoss
from label_encoder import SSDLabelEncoder
from model import SSDModel
from VGG19BaseSSD import Vgg19BaseSSD
from config import Config
import numpy as np
import os
import shutil
from box_utils import BoxUtils

device = 'cuda' if torch.cuda.is_available() else 'cpu'

class Trainer():
    
    def __init__(self, cfg):
        self.cfg = cfg
        self.prepare_data()
        self.parse_config()
    
    def build_model(self):
        if self.cfg.train_cfg.model_name == 'SSDModel':
            self.model = SSDModel(self.cfg.img_width, self.cfg.img_height, self.cfg.nclasses, 
                                  self.cfg.scales, self.cfg.aspect_ratios).to(device)
        elif self.cfg.train_cfg.model_name == 'Vgg19BaseSSD':
            self.model = Vgg19BaseSSD(self.cfg.img_width, self.cfg.img_height, self.cfg.nclasses, 
                                      self.cfg.scales, self.cfg.aspect_ratios).to(device)
        else:
            raise Exception('Model name not found!')

        checkpoint_path = os.path.join(self.cfg.train_cfg.checkpoint_dir, self.cfg.train_cfg.checkpoint_file)
        if os.path.exists(checkpoint_path):
            print("Loading checkpoint from: ", checkpoint_path)
            self.model.load_state_dict(torch.load(checkpoint_path))
        else:
            print("Checkpoint file {} don't exists".format(checkpoint_path))
    
    
    def prepare_data(self):
        train_aug = SSDDataAugmentation(target_size={'h': self.cfg.img_height, 'w': self.cfg.img_width},
                                        random_brightness={'low': -48, 'high': 48, 'prob': 0.5},
                                        random_contrast={'low': 0.5, 'high': 1.8, 'prob': 0.5},
                                        random_saturation={'low': 0.5, 'high': 1.8, 'prob': 0.5},
                                        random_hue={'low': 0.5, 'high': 1.5, 'prob': 0.5},
                                        channel_shuffle={'prob': 0.5},
                                        random_translate={'low': -0.2, 'high': 0.2, 'prob': 0.5},
                                        random_scale={'min': 0.7, 'max': 1.2, 'prob': 0.5},
                                        prob=0.8,
                                        train=True)
        
        eval_aug = SSDDataAugmentation(target_size={'h': self.cfg.img_height, 'w': self.cfg.img_width},
                                       train=False)
        train_ds = SSDDataset(self.cfg.train_cfg.data_dir,
                              self.cfg.train_cfg.train_file_path,
                              transform=transforms.Compose([train_aug,
                                                            Transpose(),
                                                            Normalization2(127.5, 127.5)
                                                            ]))
        eval_ds  = SSDDataset(self.cfg.train_cfg.data_dir,
                              self.cfg.train_cfg.eval_file_path,
                              transform=transforms.Compose([eval_aug,
                                                            Transpose(),
                                                            Normalization2(127.5, 127.5)
                                                            ]))
        
        self.train_loader = torch.utils.data.DataLoader(dataset=train_ds, batch_size=self.cfg.train_cfg.batch_size, 
                                                        collate_fn=collate_sample, shuffle=True)
        self.eval_loader  = torch.utils.data.DataLoader(dataset=eval_ds,  batch_size=self.cfg.train_cfg.batch_size,
                                                        collate_fn=collate_sample, shuffle=False)
        print("Loaded dataset, train {} samples, eval {} samples".format(len(train_ds), len(eval_ds)))


    def parse_config(self):
        self.build_model()
        self.criterion = SSDLoss(self.cfg.train_cfg.alpha, self.cfg.train_cfg.neg_pos_ratio)
        x = torch.randn(1, 3, self.cfg.img_height, self.cfg.img_width).to(device)
        predictor_shapes = self.model.get_predictor_shapes(x)
        anchor_boxes = BoxUtils.generate_anchor_boxes_model(predictor_shapes, self.cfg.scales, self.cfg.aspect_ratios)
        self.label_encoder = SSDLabelEncoder(anchor_boxes,
                                             self.cfg.nclasses, self.cfg.img_height, self.cfg.img_width, 
                                             variance=np.asarray(self.cfg.variances))
        self.optimizer = torch.optim.Adam(self.model.parameters())
        if  os.path.exists(self.cfg.train_cfg.checkpoint_dir) and os.path.isdir(self.cfg.train_cfg.checkpoint_dir):
            shutil.rmtree(self.cfg.train_cfg.checkpoint_dir)
        os.makedirs(self.cfg.train_cfg.checkpoint_dir, exist_ok=True)


    def train_on_epoch(self, epoch):
        self.model.train()
        total_loss = 0
        for sample in self.train_loader:
            images = sample['image'].to(device)
            objs   = sample['objs']
            output = self.model(images)
            labels = self.label_encoder(objs).to(device)
            loss   = self.criterion(output, labels)
            total_loss += loss.item()

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
        return total_loss / len(self.train_loader)


    def evaluate_on_epoch(self, epoch):
        self.model.eval()
        total_loss = 0
        with torch.no_grad():
            for sample in self.eval_loader:
                images = sample['image'].to(device)
                objs   = sample['objs']
                output = self.model(images)
                labels = self.label_encoder(objs).to(device)
                loss   = self.criterion(output, labels)
                total_loss += loss.item()
        return total_loss / len(self.eval_loader)


    def run(self):
        print("Start train model!")
        print("Device: ", device)
        for epoch in range(self.cfg.train_cfg.num_epochs):
            train_epoch_loss = self.train_on_epoch(epoch)
            eval_epoch_loss  = self.evaluate_on_epoch(epoch)
            print("Epoch {}, train loss {}, eval loss {}".format(epoch, train_epoch_loss, eval_epoch_loss))
            checkpoint_file  = 'ssd_{}_{:.4f}_{:.4f}.pth'.format(epoch, train_epoch_loss, eval_epoch_loss)
            torch.save(self.model.state_dict(), os.path.join(self.cfg.train_cfg.checkpoint_dir, checkpoint_file))


if __name__ == '__main__':
    cfg = Config()
    trainer = Trainer(cfg)
    trainer.run()