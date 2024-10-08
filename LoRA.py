# %% [markdown]
# Import necessary libraries
import torch
import torchvision.datasets as datasets
import torchvision.transforms as transforms
import torch.nn as nn
import matplotlib.pyplot as plt
from tqdm import tqdm

# Set random seed for reproducibility
torch.manual_seed(1337)

# %% [markdown]
# Define data transformations and load the MNIST dataset
transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(mean=(0.1307,), std=(0.3081,))  # Normalization for MNIST dataset
])

# Load MNIST training set
mnist_trainset = datasets.MNIST(
    'E:/ML/ml_projects/project_folder/gpt', 
    download=True, 
    train=True, 
    transform=transform
)
train_loader = torch.utils.data.DataLoader(mnist_trainset, batch_size=10, shuffle=True)

# Load MNIST test set
mnist_testset = datasets.MNIST(root='./data', train=False, download=True, transform=transform)
test_loader = torch.utils.data.DataLoader(mnist_testset, batch_size=10, shuffle=True)

# Set device to GPU if available
device = "cuda" if torch.cuda.is_available() else "cpu"

# %% [markdown]
# Define a simple neural network class `SimpleNN` using PyTorch
class SimpleNN(nn.Module):
    def __init__(self, hidden_size_1=1000, hidden_size_2=2000):
        super(SimpleNN, self).__init__()
        # Define layers
        self.linear1 = nn.Linear(28*28, hidden_size_1)  # Input to first hidden layer
        self.linear2 = nn.Linear(hidden_size_1, hidden_size_2)  # First to second hidden layer
        self.linear3 = nn.Linear(hidden_size_2, 10)  # Second hidden layer to output layer
        self.relu = nn.ReLU()  # Activation function

    def forward(self, img):
        # Flatten the image into a vector
        x = img.view(-1, 28*28)
        # Pass through the layers with ReLU activations
        x = self.relu(self.linear1(x))
        x = self.relu(self.linear2(x))
        x = self.linear3(x)
        return x

# Instantiate the network and move it to the device
net = SimpleNN().to(device)

# %% [markdown]
# Define a function to train the network
def train(train_loader, net, epochs=5, total_iterations_limit=None, device='cuda'):
    # Define loss function and optimizer
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(net.parameters(), lr=0.001)
    scaler = torch.cuda.amp.GradScaler()  # For mixed precision training
    
    total_iterations = 0
    
    for epoch in range(epochs):
        net.train()
        running_loss = 0.0
        
        data_iterator = tqdm(train_loader, desc=f'Epoch {epoch+1}', total=len(train_loader))
        
        if total_iterations_limit is not None:
            data_iterator.total = total_iterations_limit
        
        for i, data in enumerate(data_iterator, 0):
            inputs, labels = data
            inputs, labels = inputs.to(device), labels.to(device)
            
            optimizer.zero_grad()
            
            with torch.cuda.amp.autocast():  # Automatic Mixed Precision context
                outputs = net(inputs)
                loss = criterion(outputs, labels)
            
            scaler.scale(loss).backward()  # Scale the loss for mixed precision
            scaler.step(optimizer)
            scaler.update()
            
            running_loss += loss.item()
            data_iterator.set_postfix(loss=running_loss / (i + 1))
            
            total_iterations += 1
            
            if total_iterations_limit is not None and total_iterations >= total_iterations_limit:
                return

# Train the network
train(train_loader, net, epochs=1, device=device)

# %% [markdown]
# Save the original weights of the network
original_weights = {}
for name, param in net.named_parameters():
    original_weights[name] = param.clone().detach()

# %% [markdown]
# Define a function to evaluate the network
def eval_model():
    correct = 0
    total = 0

    wrong_counts = [0 for i in range(10)]

    with torch.no_grad():
        for data in tqdm(test_loader, desc='Testing'):
            x, y = data
            x = x.to(device)
            y = y.to(device)
            output = net(x.view(-1, 784))
            for idx, i in enumerate(output):
                if torch.argmax(i) == y[idx]:
                    correct += 1
                else:
                    wrong_counts[y[idx]] += 1
                total += 1
    print(f'Accuracy: {round(correct / total, 3)}')
    for i in range(len(wrong_counts)):
        print(f'Wrong counts for the digit {i}: {wrong_counts[i]}')

# Evaluate the network
eval_model()

# %% [markdown]
# Print the size of the weights matrices of the network and calculate total number of parameters
total_parameters_original = 0
for index, layer in enumerate([net.linear1, net.linear2, net.linear3]):
    total_parameters_original += layer.weight.nelement() + layer.bias.nelement()
    print(f'Layer {index + 1}: W: {layer.weight.shape} + B: {layer.bias.shape}')
print(f'Total number of parameters: {total_parameters_original:,}')

# %% [markdown]
# Define a LoRA parameterization class
class LoRAParametrization(nn.Module):
    def __init__(self, features_in, features_out, rank=1, alpha=1, device='cpu'):
        super().__init__()
        # Initialize LoRA parameters
        self.lora_A = nn.Parameter(torch.zeros((rank, features_out)).to(device))
        self.lora_B = nn.Parameter(torch.zeros((features_in, rank)).to(device))
        nn.init.normal_(self.lora_A, mean=0, std=1)
        
        # Scaling factor
        self.scale = alpha / rank
        self.enabled = True

    def forward(self, original_weights):
        if self.enabled:
            # Return modified weights using LoRA
            return original_weights + torch.matmul(self.lora_B, self.lora_A).view(original_weights.shape) * self.scale
        else:
            # Return original weights if LoRA is disabled
            return original_weights

# %% [markdown]
# Register the LoRA parameterization for the linear layers
import torch.nn.utils.parametrize as parametrize

def linear_layer_parameterization(layer, device, rank=1, lora_alpha=1):
    # Create a LoRA parameterization for the given layer
    features_in, features_out = layer.weight.shape
    return LoRAParametrization(
        features_in, features_out, rank=rank, alpha=lora_alpha, device=device
    )

# Apply LoRA parameterization to each linear layer
parametrize.register_parametrization(
    net.linear1, "weight", linear_layer_parameterization(net.linear1, device)
)
parametrize.register_parametrization(
    net.linear2, "weight", linear_layer_parameterization(net.linear2, device)
)
parametrize.register_parametrization(
    net.linear3, "weight", linear_layer_parameterization(net.linear3, device)
)

# Function to enable or disable LoRA
def enable_disable_lora(enabled=True):
    for layer in [net.linear1, net.linear2, net.linear3]:
        layer.parametrizations["weight"][0].enabled = enabled

# %% [markdown]
# Calculate the number of parameters with and without LoRA
total_parameters_lora = 0
total_parameters_non_lora = 0
for index, layer in enumerate([net.linear1, net.linear2, net.linear3]):
    total_parameters_lora += layer.parametrizations["weight"][0].lora_A.nelement() + layer.parametrizations["weight"][0].lora_B.nelement()
    total_parameters_non_lora += layer.weight.nelement() + layer.bias.nelement()
    print(
        f'Layer {index + 1}: W: {layer.weight.shape} + B: {layer.bias.shape} + '
        f'Lora_A: {layer.parametrizations["weight"][0].lora_A.shape} + '
        f'Lora_B: {layer.parametrizations["weight"][0].lora_B.shape}'
    )
# Ensure non-LoRA parameters match original
assert total_parameters_non_lora == total_parameters_original
print(f'Total number of parameters (original): {total_parameters_non_lora:,}')
print(f'Total number of parameters (original + LoRA): {total_parameters_lora + total_parameters_non_lora:,}')
print(f'Parameters introduced by LoRA: {total_parameters_lora:,}')
parameters_increment = (total_parameters_lora / total_parameters_non_lora) * 100
print(f'Parameters increment: {parameters_increment:.3f}%')

# %% [markdown]
# Freeze non-LoRA parameters
for name, param in net.named_parameters():
    if 'lora' not in name:
        print(f'Freezing non-LoRA parameter {name}')
        param.requires_grad = False

# Load the MNIST dataset again, keeping only the digit 9
mnist_trainset = datasets.MNIST(root='./data', train=True, download=True, transform=transform)
exclude_indices = mnist_trainset.targets == 9
mnist_trainset.data = mnist_trainset.data[exclude_indices]
mnist_trainset.targets = mnist_trainset.targets[exclude_indices]
train_loader = torch.utils.data.DataLoader(mnist_trainset, batch_size=10, shuffle=True)

# Train the network with the modified dataset
train(train_loader, net, epochs=2, total_iterations_limit=1000, device=device)

# Evaluate the network again
eval_model()
