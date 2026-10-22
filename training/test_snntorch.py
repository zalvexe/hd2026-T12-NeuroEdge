"""
Environment check for snntorch (CPU-only).

Runs a tiny spiking neural network to verify that snntorch, torch, and the
spiking neuron dynamics work end-to-end.

Usage:
    D:/MASTER/Anaconda/python.exe test_snntorch.py
"""

import platform

import torch
import torch.nn as nn
import snntorch as snn


def check_environment():
    print("=" * 60)
    print("ENVIRONMENT")
    print("=" * 60)
    print(f"Python     : {platform.python_version()} ({platform.system()})")
    print(f"Torch      : {torch.__version__}")
    print(f"snntorch   : {snn.__version__}")
    print(f"CUDA avail : {torch.cuda.is_available()} (CPU-only install expected)")
    print(f"Device     : {torch.device('cpu')}")
    print()


class TinySNN(nn.Module):
    """Fully-connected SNN: 20 -> 64 -> 32 -> 10 spiking neurons."""

    def __init__(self, num_steps=25, beta=0.9):
        super().__init__()
        self.num_steps = num_steps
        self.fc1 = nn.Linear(20, 64)
        self.lif1 = snn.Leaky(beta=beta, init_hidden=True)
        self.fc2 = nn.Linear(64, 32)
        self.lif2 = snn.Leaky(beta=beta, init_hidden=True)
        self.fc3 = nn.Linear(32, 10)
        self.lif3 = snn.Leaky(beta=beta, init_hidden=True)

    def forward(self, x):
        # Record spikes only from the output layer.
        spk_out_rec = []
        for _ in range(self.num_steps):
            spk1 = self.lif1(self.fc1(x))
            spk2 = self.lif2(self.fc2(spk1))
            spk3 = self.lif3(self.fc3(spk2))
            spk_out_rec.append(spk3)
        return torch.stack(spk_out_rec)


def check_forward_pass():
    print("=" * 60)
    print("FORWARD PASS")
    print("=" * 60)

    torch.manual_seed(0)
    net = TinySNN(num_steps=25)
    x = torch.rand(8, 20)  # batch of 8 samples, 20 features

    spk_rec = net(x)

    print(f"Input shape      : {tuple(x.shape)}")
    print(f"Spike record     : {tuple(spk_rec.shape)}  (steps, batch, classes)")
    print(f"Spike values     : unique={torch.unique(spk_rec).tolist()}")
    print(f"Firing rate      : {spk_rec.mean().item():.4f}")
    print(f"All finite       : {torch.isfinite(spk_rec).all().item()}")

    assert spk_rec.shape == (25, 8, 10), "unexpected output shape"
    assert set(torch.unique(spk_rec).tolist()).issubset({0.0, 1.0}), "non-binary spikes"
    assert 0.0 < spk_rec.mean().item() < 1.0, "network is silent or saturated"
    assert torch.isfinite(spk_rec).all(), "non-finite values produced"
    print()


def check_gradient_flow():
    print("=" * 60)
    print("GRADIENT FLOW (surrogate gradient)")
    print("=" * 60)

    torch.manual_seed(1)
    net = TinySNN(num_steps=10)
    x = torch.rand(4, 20)
    target = torch.randint(0, 10, (4,))

    spk_rec = net(x)
    loss = nn.functional.cross_entropy(spk_rec.sum(0), target)
    loss.backward()

    grad_norm = sum(
        p.grad.norm().item() for p in net.parameters() if p.grad is not None
    )
    print(f"Loss             : {loss.item():.4f}")
    print(f"Total grad norm  : {grad_norm:.4f}")

    assert grad_norm > 0, "no gradients propagated (surrogate gradient broken)"
    print()


def main():
    check_environment()
    check_forward_pass()
    check_gradient_flow()
    print("=" * 60)
    print("RESULT: snntorch environment is working correctly (CPU).")
    print("=" * 60)


if __name__ == "__main__":
    main()
