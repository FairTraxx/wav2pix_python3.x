import numpy as np
from torch import nn
from torch import  autograd
import torch
from scripts.visualize import VisdomPlotter
import os
import logging


def from_onehot_to_int(tensor):
    '''
    what this function does is finding the scalar value representing the class
    (youtuber) which the audio in question belongs to
    '''
    matrix = tensor.numpy()
    int_list = []
    for row in matrix:
        int_list.append(int(np.where(row == 1)[0]))
    return torch.from_numpy(np.array(int_list))


class Concat_embed(nn.Module):

    def __init__(self, embed_dim, projected_embed_dim, dataset='youtubers'):
        super(Concat_embed, self).__init__()
        if dataset != 'youtubers':
            self.projection = nn.Sequential(
                nn.Linear(in_features=embed_dim, out_features=projected_embed_dim),
                nn.BatchNorm1d(num_features=projected_embed_dim),
                nn.LeakyReLU(negative_slope=0.2, inplace=True)
                )
        else:
            self.projection = nn.Sequential(
                nn.Linear(in_features=62, out_features=projected_embed_dim),
                nn.BatchNorm1d(num_features=projected_embed_dim),
                nn.LeakyReLU(negative_slope=0.2, inplace=True)
            )

    def forward(self, inp, projected_embed):
        #projected_embed = self.projection(embed)

        replicated_embed = projected_embed.repeat(1, 1, 4, 4)#.permute(2, 3, 0, 1)
        #print(inp)
        hidden_concat = torch.cat([inp, replicated_embed], 1)

        return hidden_concat


class minibatch_discriminator(nn.Module):
    def __init__(self, num_channels, B_dim, C_dim):
        super(minibatch_discriminator, self).__init__()
        self.B_dim = B_dim
        self.C_dim =C_dim
        self.num_channels = num_channels
        T_init = torch.randn(num_channels * 4 * 4, B_dim * C_dim) * 0.1
        self.T_tensor = nn.Parameter(T_init, requires_grad=True)

    def forward(self, inp):
        inp = inp.view(-1, self.num_channels * 4 * 4)
        M = inp.mm(self.T_tensor)
        M = M.view(-1, self.B_dim, self.C_dim)

        op1 = M.unsqueeze(3)
        op2 = M.permute(1, 2, 0).unsqueeze(0)

        output = torch.sum(torch.abs(op1 - op2), 2)
        output = torch.sum(torch.exp(-output), 2)
        output = output.view(M.size(0), -1)

        output = torch.cat((inp, output), 1)

        return output


class Utils(object):

    @staticmethod
    def smooth_label(tensor, offset):
        return tensor + offset

    @staticmethod
    # based on:  https://github.com/caogang/wgan-gp/blob/master/gan_cifar10.py
    def compute_GP(netD, real_data, real_embed, fake_data, LAMBDA, project=False):
        #TODO: Should be improved!!!! Maybe using: https://github.com/EmilienDupont/wgan-gp/blob/master/training.py
        BATCH_SIZE = real_data.size(0)
        alpha = torch.rand(BATCH_SIZE, 1)
        alpha = alpha.expand(real_data.size())
        alpha = alpha.cuda()

        interpolates = alpha * real_data + ((1 - alpha) * fake_data)

        interpolates = interpolates.cuda()

        interpolates = autograd.Variable(interpolates, requires_grad=True)
        disc_interpolates, _ = netD(interpolates, real_embed, project=project)

        gradients = autograd.grad(outputs=disc_interpolates, inputs=interpolates,
                                  grad_outputs=torch.ones(disc_interpolates.size()).cuda(),
                                  create_graph=True, retain_graph=True, only_inputs=True)[0]

        gradient_penalty = ((gradients.norm(2, dim=1) - 1) ** 2).mean() * LAMBDA

        return gradient_penalty

    @staticmethod
    def save_checkpoint(netD, netG, dir_path, subdir_path, epoch):
        path = os.path.join(dir_path, subdir_path)
        if not os.path.exists(path):
            os.makedirs(path)

        torch.save(netD.state_dict(), '{0}/disc_{1}.pth'.format(path, epoch))
        torch.save(netG.state_dict(), '{0}/gen_{1}.pth'.format(path, epoch))

    @staticmethod
    def weights_init(m):
        classname = m.__class__.__name__  # object.__class__.__name__ will give the name of the class of an object.

        if hasattr(m, 'weight') and (classname.find('Conv') != -1 or classname.find('Linear') != -1):  # find returns ndex if found and -1 otherwise.
            m.weight.data.normal_(0.0, 0.02)
        if classname.find('BatchNorm') != -1:
            m.weight.data.normal_(1.0, 0.02)
            m.bias.data.fill_(0)


class Logger(object):
    def __init__(self, vis_screen, save_path):
        self.viz = VisdomPlotter(env_name=vis_screen)
        self.hist_D = []
        self.hist_G = []
        self.hist_Dx = []
        self.hist_DGx = []
        self.hist_wrongDx = []
        self.logger = logging.getLogger('lossesLogger')
        self.logFile = os.path.join('logs', save_path)
        if not os.path.exists(self.logFile):
            os.makedirs(self.logFile)
        handler = logging.FileHandler(self.logFile+'/logFile.log')
        handler.setLevel(logging.INFO)
        self.logger.addHandler(hdlr=handler)
        self.logger.setLevel(logging.INFO)


    def log_iteration_gan(self, epoch, d_loss, g_loss, real_score, fake_score, wrong_score):
        print(("Epoch: %d, d_loss= %f, g_loss= %f, real score D(X)= %f, fake score D(G(X))= %f, wrong score: %f" % (
            epoch, d_loss.data.cpu().mean(), g_loss.data.cpu().mean(), real_score.data.cpu().mean(),
            fake_score.data.cpu().mean(), wrong_score.data.cpu().mean())))
        self.hist_D.append(d_loss.data.cpu().mean())
        self.hist_G.append(g_loss.data.cpu().mean())
        self.hist_Dx.append(real_score.data.cpu().mean())
        self.hist_DGx.append(fake_score.data.cpu().mean())
        self.hist_wrongDx.append(wrong_score.data.cpu().mean())
        self.logger.info("Epoch: %d, d_loss= %f, g_loss= %f, D(X)= %f, D(G(X))= %f, wrong score: %f" %
                         (epoch, d_loss.data.cpu().mean(), g_loss.data.cpu().mean(), real_score.data.cpu().mean(),
                          fake_score.data.cpu().mean(), wrong_score.data.cpu().mean()))

    def plot_epoch(self, epoch):
        self.viz.plot('Discriminator', 'train', epoch, np.array(self.hist_D).mean())
        self.viz.plot('Generator', 'train', epoch, np.array(self.hist_G).mean())
        self.hist_D = []
        self.hist_G = []

    def plot_epoch_w_scores(self, epoch):
        self.viz.plot('Discriminator', 'train', epoch, np.array(self.hist_D).mean())
        self.viz.plot('Generator', 'train', epoch, np.array(self.hist_G).mean())
        self.viz.plot('D(X)', 'train', epoch, np.array(self.hist_Dx).mean())
        self.viz.plot('D(G(X))', 'train', epoch, np.array(self.hist_DGx).mean())
        self.hist_D = []
        self.hist_G = []
        self.hist_Dx = []
        self.hist_DGx = []

    def draw(self, right_images, fake_images):
        self.viz.draw('generated images', fake_images.data.cpu().numpy()[:64] * 128 + 128)
        self.viz.draw('real images', right_images.data.cpu().numpy()[:64] * 128 + 128)
