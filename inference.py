import matplotlib
matplotlib.use("Agg")
import matplotlib.pylab as plt
import argparse
import os

import numpy as np
import time
import torch

from hparams import create_hparams
from layers import TacotronSTFT
from audio_processing import griffin_lim, mel_denormalize
from train import load_model
from text import text_to_sequence
from scipy.io.wavfile import write
from utils import load_wav_to_torch

def plot_data(data, index, output_dir="", figsize=(16, 4)):
    fig, axes = plt.subplots(1, len(data), figsize=figsize)
    for i in range(len(data)):
        axes[i].imshow(data[i], aspect='auto', origin='bottom',
                        interpolation='none')
    plt.savefig(os.path.join(output_dir, 'sentence_{}.png'.format(index)))

def get_mel(stft, filename, hparams):
    audio, sampling_rate = load_wav_to_torch(filename)
    if sampling_rate != hparams.sampling_rate:
        raise ValueError("{} SR doesn't match target {} SR".format(
            sampling_rate, hparams.sampling_rate))
    audio_norm = audio / hparams.max_wav_value
    audio_norm = audio_norm.unsqueeze(0)
    audio_norm = torch.autograd.Variable(audio_norm, requires_grad=False)
    melspec = stft.mel_spectrogram(audio_norm)
    return melspec

def generate_mels(hparams, checkpoint_path, audio_paths, silence_mel_padding, stft, output_dir=""):
    model = load_model(hparams)
    try:
        model = model.module
    except:
        pass
    model.load_state_dict({k.replace('module.', ''): v for k, v in torch.load(checkpoint_path)['state_dict'].items()})
    _ = model.eval()
    output_mels = []
    for i, a in enumerate(audio_paths):
        source_mel = get_mel(stft, a, hparams).cuda()
        stime = time.time()
        _, mel_outputs_postnet, _, alignments = model.inference(source_mel)
        plot_data((source_mel.data.cpu().numpy()[0], mel_outputs_postnet.data.cpu().numpy()[0],
                   alignments.data.cpu().numpy()[0].T), i, output_dir)
        inf_time = time.time() - stime
        print("{}th sentence, Infenrece time: {:.2f}s, len_mel: {}".format(i, inf_time, mel_outputs_postnet.size(2)))
        output_mels.append(mel_outputs_postnet[:,:,:-silence_mel_padding])
    return output_mels

def mels_to_wavs_GL(hparams, mels, taco_stft, output_dir="", ref_level_db = 0, magnitude_power=1.5):
    for i, mel in enumerate(mels):
        stime = time.time()
        mel_decompress = mel_denormalize(mel)
        mel_decompress = taco_stft.spectral_de_normalize(mel_decompress + ref_level_db) ** (1/magnitude_power)
        mel_decompress = mel_decompress.transpose(1, 2).data.cpu()
        spec_from_mel_scaling = 1000
        spec_from_mel = torch.mm(mel_decompress[0], taco_stft.mel_basis)
        spec_from_mel = spec_from_mel.transpose(0, 1).unsqueeze(0)
        spec_from_mel = spec_from_mel * spec_from_mel_scaling
        waveform = griffin_lim(torch.autograd.Variable(spec_from_mel[:, :, :]),
                               taco_stft.stft_fn, 60)
        waveform = waveform[0].data.cpu().numpy()
        dec_time = time.time() - stime
        len_audio = float(len(waveform)) / float(hparams.sampling_rate)
        str = "{}th sentence, audio length: {:.2f} sec,  mel_to_wave time: {:.2f}".format(i, len_audio, dec_time)
        print(str)
        write(os.path.join(output_dir,"sentence_{}.wav".format(i)), hparams.sampling_rate, waveform)

def run(hparams, checkpoint_path, audio_path_file, silence_mel_padding, output_dir):
    f = open(audio_path_file, 'r')
    audio_paths = [x.strip() for x in f.readlines()]
    print('All sentences to infer:',audio_paths)
    f.close()

    stft = TacotronSTFT(
        hparams.filter_length, hparams.hop_length, hparams.win_length,
        hparams.n_mel_channels, hparams.sampling_rate, hparams.mel_fmin,
        hparams.mel_fmax)

    mels = generate_mels(hparams, checkpoint_path, audio_paths, silence_mel_padding, stft, output_dir)
    mels_to_wavs_GL(hparams, mels, stft, output_dir)
    pass

if __name__ == '__main__':
    """
    usage
    python inference.py -o=synthesis/80000 -c=nam_h_ep8/checkpoint_80000 -a=test.txt --silence_mel_padding=3
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output_directory', type=str,
                        help='directory to save wave and fig')
    parser.add_argument('-c', '--checkpoint_path', type=str, default=None,
                        required=True, help='checkpoint path')
    parser.add_argument('-a', '--audio_path_file', type=str, default=None,
                        required=True, help='sentence path')
    parser.add_argument('--silence_mel_padding', type=int, default=0,
                        help='silence audio size is hop_length * silence mel padding')
    parser.add_argument('--hparams', type=str,
                        required=False, help='comma separated name=value pairs')

    args = parser.parse_args()
    hparams = create_hparams(args.hparams)
    hparams.sampling_rate = 22050
    hparams.filter_length = 1024
    hparams.hop_length = 256
    hparams.win_length = 1024

    torch.backends.cudnn.enabled = hparams.cudnn_enabled
    torch.backends.cudnn.benchmark = hparams.cudnn_benchmark

    run(hparams, args.checkpoint_path, args.audio_path_file, args.silence_mel_padding ,args.output_directory)



